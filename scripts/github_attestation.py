"""Authenticate provenance decisions raised through the GitHub issue form."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen

from provenance import GITHUB_REPOSITORY, load_provenance_sidecars
from verifier_policy import PolicyError, load_verifier_policy


API_ROOT = "https://api.github.com"
SIGNOFF_LABEL = "provenance sign-off"
SIGNOFF_TITLE = "[provenance sign-off]"
REVISION = re.compile(r"^sha256:[0-9a-f]{64}$")
RECORD_PATH = re.compile(
    r"^records/[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*)*$"
)
ISSUE_FIELDS = {
    "Record",
    "Evidence revision",
    "Verifier policy revision",
    "Decision",
    "Supersedes issue",
    "Review note",
}


class AttestationError(ValueError):
    """A GitHub issue cannot authenticate the claimed verifier decision."""


def parse_signoff_issue_body(body):
    """Parse the exact fields emitted by the provenance sign-off issue form."""
    if not isinstance(body, str):
        raise AttestationError("sign-off issue body is missing")
    fields = {}
    for match in re.finditer(
        r"^### ([^\n]+)\n\n(.*?)(?=^### [^\n]+\n\n|\Z)",
        body,
        flags=re.MULTILINE | re.DOTALL,
    ):
        label = match.group(1).strip()
        if label in fields:
            raise AttestationError(f"duplicate issue field: {label}")
        fields[label] = match.group(2).strip()
    if set(fields) != ISSUE_FIELDS:
        raise AttestationError("sign-off issue fields do not match the template")

    decisions = {
        "Verified": "verified",
        "Changes requested": "changes_requested",
        "Unresolved": "unresolved",
        "Curation required": "curation_required",
        "Withdrawn": "withdrawn",
    }
    record = fields["Record"]
    evidence = fields["Evidence revision"]
    policy_revision = fields["Verifier policy revision"]
    decision = decisions.get(fields["Decision"])
    if not RECORD_PATH.fullmatch(record):
        raise AttestationError("invalid provenance record path")
    if not REVISION.fullmatch(evidence):
        raise AttestationError("invalid evidence revision")
    if not REVISION.fullmatch(policy_revision):
        raise AttestationError("invalid verifier policy revision")
    if decision is None:
        raise AttestationError("invalid sign-off decision")

    raw_supersedes = fields["Supersedes issue"]
    supersedes = None
    if raw_supersedes not in {"", "_No response_"}:
        try:
            issue_id = int(raw_supersedes.removeprefix("#"))
        except ValueError as exc:
            raise AttestationError("superseded issue must be a number") from exc
        if issue_id < 1:
            raise AttestationError("superseded issue must be positive")
        supersedes = {"kind": "issue", "id": issue_id}
    if decision == "withdrawn" and supersedes is None:
        raise AttestationError("withdrawal must supersede an earlier issue")
    return {
        "provenance_record": record,
        "decision": decision,
        "evidence_revision": evidence,
        "verifier_policy_revision": policy_revision,
        "scope": "record",
        "supersedes_event": supersedes,
        "note": fields["Review note"],
    }


def fact_from_signoff_issue(issue, policy):
    """Authenticate one issue-form decision against the verifier registry."""
    if not isinstance(issue, dict) or issue.get("pull_request"):
        raise AttestationError("GitHub response is not an issue")
    if not str(issue.get("title", "")).startswith(SIGNOFF_TITLE):
        raise AttestationError("issue title is not a provenance sign-off")
    labels = {
        item.get("name") for item in issue.get("labels", []) if isinstance(item, dict)
    }
    if SIGNOFF_LABEL not in labels:
        raise AttestationError("issue lacks the provenance sign-off label")

    user = issue.get("user")
    if not isinstance(user, dict) or user.get("type") != "User":
        raise AttestationError("sign-off author must be a GitHub user")
    login = user.get("login")
    user_id = user.get("id")
    verifier = policy["verifiers"].get(str(login).casefold())
    if (
        not isinstance(login, str)
        or not isinstance(user_id, int)
        or not verifier
        or verifier["github_user_id"] != user_id
        or verifier["github_handle"].casefold() != login.casefold()
    ):
        raise AttestationError("GitHub author is not an eligible verifier")

    issue_id = issue.get("number")
    url = issue.get("html_url")
    if (
        not isinstance(issue_id, int)
        or url != f"https://github.com/{GITHUB_REPOSITORY}/issues/{issue_id}"
    ):
        raise AttestationError("GitHub issue number and URL do not match")
    payload = parse_signoff_issue_body(issue.get("body"))
    if payload["scope"] not in verifier["scopes"]:
        raise AttestationError("verifier is not eligible for this scope")
    return {
        "author": login,
        "author_id": user_id,
        "signed_at": issue.get("created_at"),
        "url": url,
        "repository": policy["repository"],
        **payload,
    }


def attestation_from_issue(issue, policy):
    """Return the decision shape consumed by site state derivation."""
    fact = fact_from_signoff_issue(issue, policy)
    item = {
        "verifier": fact["author"],
        "verifier_id": fact["author_id"],
        "decision": fact["decision"],
        "signed_at": fact["signed_at"],
        "event": {"kind": "issue", "id": issue["number"], "url": fact["url"]},
        "evidence_revision": fact["evidence_revision"],
        "verifier_policy_revision": fact["verifier_policy_revision"],
        "scope": fact["scope"],
    }
    if fact["supersedes_event"]:
        item["supersedes_event"] = fact["supersedes_event"]
    if fact["note"] not in {"", "_No response_"}:
        item["note"] = fact["note"]
    return fact["provenance_record"], item


def collect_issue_attestations(issues, policy, sidecars, require_current=False):
    """Group valid decisions by record and return rejected issue reasons."""
    grouped = defaultdict(list)
    errors = []
    for issue in issues:
        issue_id = issue.get("number") if isinstance(issue, dict) else None
        try:
            record_path, item = attestation_from_issue(issue, policy)
            sidecar = sidecars.get(record_path)
            if not sidecar:
                raise AttestationError("sign-off record has no provenance assessment")
            if require_current and (
                item["evidence_revision"]
                != sidecar["assessment"].get("evidence_revision")
                or item["verifier_policy_revision"] != policy["revision"]
            ):
                raise AttestationError("sign-off revision is stale")
            grouped[record_path].append(item)
        except AttestationError as exc:
            errors.append(f"issue #{issue_id}: {exc}")
    return dict(grouped), errors


def _github_json(path, token):
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "SUEWS-provenance-checker",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    request = Request(f"{API_ROOT}{path}", headers=headers)
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def fetch_issue_attestations(token, policy, sidecars):
    """Sweep every labelled sign-off issue for a site build."""
    issues = []
    page = 1
    while True:
        batch = _github_json(
            f"/repos/{GITHUB_REPOSITORY}/issues?state=all&labels="
            f"provenance%20sign-off&per_page=100&page={page}",
            token,
        )
        if not isinstance(batch, list):
            raise AttestationError("GitHub issue list response is not an array")
        issues.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return collect_issue_attestations(issues, policy, sidecars)


def validate_issue_event(event_path, policy, sidecars):
    """Validate the issue event that triggered trusted sign-off CI."""
    try:
        event = json.loads(Path(event_path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AttestationError(f"cannot read GitHub event: {exc}") from exc
    if event.get("repository", {}).get("full_name") != GITHUB_REPOSITORY:
        raise AttestationError("event belongs to another repository")
    issue = event.get("issue")
    sender = event.get("sender", {})
    user = issue.get("user", {}) if isinstance(issue, dict) else {}
    if (
        sender.get("id") != user.get("id")
        or str(sender.get("login", "")).casefold()
        != str(user.get("login", "")).casefold()
    ):
        raise AttestationError("event sender does not match issue author")
    grouped, errors = collect_issue_attestations(
        [issue], policy, sidecars, require_current=True
    )
    if errors:
        raise AttestationError(errors[0])
    if sum(len(items) for items in grouped.values()) != 1:
        raise AttestationError("event did not produce one verifier decision")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", nargs="?", choices=("check", "validate-issue"), default="check"
    )
    parser.add_argument("event_path", nargs="?")
    args = parser.parse_args(argv)
    try:
        policy = load_verifier_policy()
    except PolicyError as exc:
        print(f"verifier policy: {exc}", file=sys.stderr)
        return 1
    sidecars, errors = load_provenance_sidecars()
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    if args.command == "check":
        print(
            f"github sign-off: {len(policy['verifiers'])} eligible verifier(s), "
            f"{len(sidecars)} assessed record(s)"
        )
        return 0
    try:
        if not args.event_path:
            raise AttestationError("validate-issue requires an event path")
        validate_issue_event(args.event_path, policy, sidecars)
    except AttestationError as exc:
        print(f"provenance sign-off rejected: {exc}", file=sys.stderr)
        return 1
    print("provenance sign-off accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
