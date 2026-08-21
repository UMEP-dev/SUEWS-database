"""Authenticate provenance sign-offs against immutable GitHub issue comments.

The sidecar is not proof of identity. This module reloads each referenced
comment from GitHub, parses its exact signed payload, and checks the actor
against the reviewed verifier policy.
"""

from __future__ import annotations

import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from provenance import GITHUB_REPOSITORY, load_provenance_sidecars
from verifier_policy import PolicyError, load_verifier_policy


API_ROOT = "https://api.github.com"
BODY_PREFIX = "SUEWS provenance sign-off\n\n```json\n"
BODY_SUFFIX = "\n```"
REVISION = re.compile(r"^sha256:[0-9a-f]{64}$")
RECORD_PATH = re.compile(
    r"^records/[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)*$"
)
DECISIONS = {
    "verified",
    "changes_requested",
    "unresolved",
    "curation_required",
    "withdrawn",
}
PAYLOAD_KEYS = {
    "version",
    "provenance_record",
    "decision",
    "evidence_revision",
    "verifier_policy_revision",
    "scope",
    "supersedes_event",
}


class AttestationError(ValueError):
    """A GitHub event cannot authenticate the claimed attestation."""


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AttestationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_signoff_body(body):
    """Parse the exact, human-visible JSON envelope posted by the button."""
    if not isinstance(body, str) or not (
        body.startswith(BODY_PREFIX) and body.endswith(BODY_SUFFIX)
    ):
        raise AttestationError("comment is not an exact SUEWS sign-off payload")
    raw = body[len(BODY_PREFIX) : -len(BODY_SUFFIX)]
    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AttestationError(f"invalid sign-off JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != PAYLOAD_KEYS:
        raise AttestationError("sign-off payload fields do not match version 1")
    if payload["version"] != 1:
        raise AttestationError("unsupported sign-off payload version")
    if not RECORD_PATH.fullmatch(str(payload["provenance_record"])):
        raise AttestationError("invalid provenance record path")
    if payload["decision"] not in DECISIONS:
        raise AttestationError("invalid sign-off decision")
    if not REVISION.fullmatch(str(payload["evidence_revision"])):
        raise AttestationError("invalid evidence revision")
    if not REVISION.fullmatch(str(payload["verifier_policy_revision"])):
        raise AttestationError("invalid verifier policy revision")
    if payload["scope"] != "record":
        raise AttestationError("version 1 supports record scope only")

    supersedes = payload["supersedes_event"]
    if supersedes is not None:
        if (
            not isinstance(supersedes, dict)
            or set(supersedes) != {"kind", "id"}
            or supersedes["kind"] != "issue_comment"
            or not isinstance(supersedes["id"], int)
            or isinstance(supersedes["id"], bool)
            or supersedes["id"] < 1
        ):
            raise AttestationError("invalid superseded event")
    if payload["decision"] == "withdrawn" and supersedes is None:
        raise AttestationError("withdrawal must supersede an earlier event")
    return payload


def fact_from_issue_comment(comment, policy):
    """Return authenticated event facts from a GitHub REST comment object."""
    if not isinstance(comment, dict):
        raise AttestationError("GitHub comment response is not an object")
    user = comment.get("user")
    if not isinstance(user, dict) or user.get("type") != "User":
        raise AttestationError("sign-off actor must be a GitHub user")
    login = user.get("login")
    user_id = user.get("id")
    if not isinstance(login, str) or not isinstance(user_id, int):
        raise AttestationError("GitHub comment has no stable actor identity")
    if comment.get("created_at") != comment.get("updated_at"):
        raise AttestationError("edited comments cannot authenticate sign-off")

    event_id = comment.get("id")
    url = comment.get("html_url")
    if (
        not isinstance(event_id, int)
        or not isinstance(url, str)
        or not url.startswith(
            f"https://github.com/{GITHUB_REPOSITORY}/"
        )
        or not url.endswith(f"#issuecomment-{event_id}")
    ):
        raise AttestationError("GitHub event ID and URL do not match")

    payload = parse_signoff_body(comment.get("body"))
    if payload["verifier_policy_revision"] == policy["revision"]:
        verifier = policy["verifiers"].get(login.casefold())
        if (
            not verifier
            or verifier["github_user_id"] != user_id
            or verifier["github_handle"].casefold() != login.casefold()
        ):
            raise AttestationError("GitHub actor is not an eligible verifier")
        if payload["scope"] not in verifier["scopes"]:
            raise AttestationError("verifier is not eligible for this scope")

    return {
        "author": login,
        "author_id": user_id,
        "signed_at": comment.get("created_at"),
        "url": url,
        "repository": policy["repository"],
        "decision": payload["decision"],
        "provenance_record": payload["provenance_record"],
        "evidence_revision": payload["evidence_revision"],
        "verifier_policy_revision": payload["verifier_policy_revision"],
        "scope": payload["scope"],
        "supersedes_event": payload["supersedes_event"],
    }


def _attestation_matches_fact(
    attestation, fact, provenance_record, current_policy_revision
):
    event = attestation.get("event", {})
    same_handle = (
        attestation.get("verifier", "").casefold()
        == fact.get("author", "").casefold()
    )
    current_policy = (
        attestation.get("verifier_policy_revision")
        == current_policy_revision
    )
    return (
        attestation.get("verifier_id") == fact.get("author_id")
        and (same_handle or not current_policy)
        and attestation.get("signed_at") == fact.get("signed_at")
        and event.get("kind") == "issue_comment"
        and event.get("url") == fact.get("url")
        and attestation.get("decision") == fact.get("decision")
        and provenance_record == fact.get("provenance_record")
        and attestation.get("evidence_revision")
        == fact.get("evidence_revision")
        and attestation.get("verifier_policy_revision")
        == fact.get("verifier_policy_revision")
        and attestation.get("scope") == fact.get("scope")
        and attestation.get("supersedes_event")
        == fact.get("supersedes_event")
    )


def _github_json(path, token):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "SUEWS-provenance-checker",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{API_ROOT}{path}", headers=headers)
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def check_github_attestations(sidecars, policy, fetch_comment):
    """Authenticate every stored attestation; return errors, never partial trust."""
    errors = []
    checked = 0
    for record_path, sidecar in sorted(sidecars.items()):
        for item in sidecar.get("verification", {}).get("attestations", []):
            checked += 1
            event = item.get("event", {})
            label = f"provenance/{record_path}"
            if event.get("kind") != "issue_comment":
                errors.append(f"{label}: only issue_comment sign-offs are supported")
                continue
            event_id = event.get("id")
            try:
                comment = fetch_comment(event_id)
                if not isinstance(comment, dict):
                    raise AttestationError(
                        "GitHub comment response is not an object"
                    )
                if comment.get("id") != event_id:
                    raise AttestationError("GitHub returned a different event ID")
                fact = fact_from_issue_comment(comment, policy)
                if not _attestation_matches_fact(
                    item, fact, record_path, policy["revision"]
                ):
                    raise AttestationError(
                        "sidecar attestation does not match authenticated event"
                    )
            except (AttestationError, HTTPError, URLError, OSError) as exc:
                errors.append(f"{label}: event issue_comment:{event_id}: {exc}")
    return checked, errors


def main():
    try:
        policy = load_verifier_policy()
    except PolicyError as exc:
        print(f"verifier policy: {exc}", file=sys.stderr)
        return 1
    sidecars, errors = load_provenance_sidecars()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    def fetch_comment(event_id):
        if not token:
            raise AttestationError(
                "GITHUB_TOKEN is required when attestations are present"
            )
        return _github_json(
            f"/repos/{GITHUB_REPOSITORY}/issues/comments/{event_id}", token
        )

    checked, auth_errors = check_github_attestations(
        sidecars, policy, fetch_comment
    )
    errors.extend(auth_errors)
    print(f"github sign-off: {checked} attestations checked, {len(errors)} errors")
    for error in errors:
        print("  -", error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
