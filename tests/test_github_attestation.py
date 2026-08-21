from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from github_attestation import (  # noqa: E402
    AttestationError,
    BODY_PREFIX,
    BODY_SUFFIX,
    check_github_attestations,
    fact_from_issue_comment,
    parse_signoff_body,
)
from verifier_policy import PolicyError, load_verifier_policy  # noqa: E402


POLICY_REVISION = "sha256:" + "1" * 64
EVIDENCE_REVISION = "sha256:" + "2" * 64
RECORD = "records/ohm/example"


def policy():
    return {
        "revision": POLICY_REVISION,
        "repository": "UMEP-dev/SUEWS-database",
        "required_signoffs": 1,
        "required_scopes": ("record",),
        "verifiers": {
            "sunt05": {
                "github_handle": "sunt05",
                "github_user_id": 1802656,
                "scopes": ("record",),
            }
        },
    }


def payload(**changes):
    value = {
        "version": 1,
        "provenance_record": RECORD,
        "decision": "verified",
        "evidence_revision": EVIDENCE_REVISION,
        "verifier_policy_revision": POLICY_REVISION,
        "scope": "record",
        "supersedes_event": None,
    }
    value.update(changes)
    return value


def body(value=None):
    value = payload() if value is None else value
    return (
        BODY_PREFIX
        + json.dumps(value, separators=(",", ":"))
        + BODY_SUFFIX
    )


def comment(**changes):
    value = {
        "id": 101,
        "html_url": (
            "https://github.com/UMEP-dev/SUEWS-database/issues/22"
            "#issuecomment-101"
        ),
        "body": body(),
        "created_at": "2026-08-21T15:00:00Z",
        "updated_at": "2026-08-21T15:00:00Z",
        "user": {"login": "sunt05", "id": 1802656, "type": "User"},
    }
    value.update(changes)
    return value


def attestation():
    return {
        "verifier": "sunt05",
        "verifier_id": 1802656,
        "decision": "verified",
        "signed_at": "2026-08-21T15:00:00Z",
        "event": {
            "kind": "issue_comment",
            "id": 101,
            "url": (
                "https://github.com/UMEP-dev/SUEWS-database/issues/22"
                "#issuecomment-101"
            ),
        },
        "evidence_revision": EVIDENCE_REVISION,
        "verifier_policy_revision": POLICY_REVISION,
        "scope": "record",
    }


class PolicyTests(unittest.TestCase):
    def test_repository_policy_has_stable_ids_and_derived_revision(self):
        loaded = load_verifier_policy()
        self.assertEqual(loaded["verifiers"]["sunt05"]["github_user_id"], 1802656)
        self.assertEqual(
            loaded["verifiers"]["suegrimmond"]["github_user_id"], 20229342
        )
        self.assertRegex(loaded["revision"], r"^sha256:[0-9a-f]{64}$")

    def test_duplicate_identity_is_rejected(self):
        document = yaml.safe_load(
            (ROOT / ".github/provenance-verifiers.yml").read_text()
        )
        document["verifiers"].append(deepcopy(document["verifiers"][0]))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.yml"
            path.write_text(yaml.safe_dump(document, sort_keys=False))
            with self.assertRaises(PolicyError):
                load_verifier_policy(path=path)

    def test_impossible_threshold_is_rejected(self):
        document = yaml.safe_load(
            (ROOT / ".github/provenance-verifiers.yml").read_text()
        )
        document["required_signoffs"] = 3
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.yml"
            path.write_text(yaml.safe_dump(document, sort_keys=False))
            with self.assertRaisesRegex(PolicyError, "below required_signoffs"):
                load_verifier_policy(path=path)


class EventTests(unittest.TestCase):
    def test_exact_authenticated_comment_is_accepted(self):
        fact = fact_from_issue_comment(comment(), policy())
        self.assertEqual(fact["author_id"], 1802656)
        self.assertEqual(fact["provenance_record"], RECORD)

        sidecars = {
            RECORD: {"verification": {"attestations": [attestation()]}}
        }
        checked, errors = check_github_attestations(
            sidecars, policy(), lambda event_id: comment()
        )
        self.assertEqual(checked, 1)
        self.assertEqual(errors, [])

    def test_unauthorized_or_renamed_identity_is_rejected(self):
        for user in (
            {"login": "other", "id": 1802656, "type": "User"},
            {"login": "sunt05", "id": 999, "type": "User"},
            {"login": "sunt05", "id": 1802656, "type": "Bot"},
        ):
            with self.subTest(user=user), self.assertRaises(AttestationError):
                fact_from_issue_comment(comment(user=user), policy())

    def test_stale_event_is_authenticated_but_edited_event_is_rejected(self):
        stale_revision = "sha256:" + "9" * 64
        stale = comment(
            body=body(payload(verifier_policy_revision=stale_revision))
        )
        fact = fact_from_issue_comment(stale, policy())
        self.assertEqual(fact["verifier_policy_revision"], stale_revision)

        historical = attestation()
        historical["verifier_policy_revision"] = stale_revision
        historical_sidecars = {
            RECORD: {"verification": {"attestations": [historical]}}
        }
        renamed = deepcopy(stale)
        renamed["user"]["login"] = "renamed-handle"
        checked, errors = check_github_attestations(
            historical_sidecars, policy(), lambda event_id: renamed
        )
        self.assertEqual(checked, 1)
        self.assertEqual(errors, [])

        with self.assertRaisesRegex(AttestationError, "edited comments"):
            fact_from_issue_comment(
                comment(updated_at="2026-08-21T15:01:00Z"), policy()
            )

    def test_payload_rejects_extra_and_duplicate_fields(self):
        extra = payload(extra=True)
        with self.assertRaises(AttestationError):
            parse_signoff_body(body(extra))
        duplicate = BODY_PREFIX + '{"version":1,"version":1}' + BODY_SUFFIX
        with self.assertRaisesRegex(AttestationError, "duplicate JSON key"):
            parse_signoff_body(duplicate)


if __name__ == "__main__":
    unittest.main()
