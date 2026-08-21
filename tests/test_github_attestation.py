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
    attestation_from_issue,
    collect_issue_attestations,
    fact_from_signoff_issue,
    parse_signoff_issue_body,
    validate_issue_event,
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


def issue_body(**changes):
    fields = {
        "Record": RECORD,
        "Evidence revision": EVIDENCE_REVISION,
        "Verifier policy revision": POLICY_REVISION,
        "Decision": "Verified",
        "Supersedes issue": "_No response_",
        "Review note": "Checked against the cited source.",
    }
    fields.update(changes)
    return "\n\n".join(f"### {key}\n\n{value}" for key, value in fields.items())


def issue(**changes):
    value = {
        "number": 201,
        "title": "[provenance sign-off] example",
        "html_url": "https://github.com/UMEP-dev/SUEWS-database/issues/201",
        "body": issue_body(),
        "created_at": "2026-08-21T16:00:00Z",
        "updated_at": "2026-08-21T16:00:00Z",
        "labels": [{"name": "provenance sign-off"}],
        "user": {"login": "sunt05", "id": 1802656, "type": "User"},
    }
    value.update(changes)
    return value


class PolicyTests(unittest.TestCase):
    def test_repository_policy_has_stable_ids_and_derived_revision(self):
        loaded = load_verifier_policy()
        self.assertEqual(loaded["verifiers"]["sunt05"]["github_user_id"], 1802656)
        self.assertEqual(
            loaded["verifiers"]["suegrimmond"]["github_user_id"], 20229342
        )
        self.assertRegex(loaded["revision"], r"^sha256:[0-9a-f]{64}$")

    def test_duplicate_or_impossible_policy_is_rejected(self):
        document = yaml.safe_load(
            (ROOT / ".github/provenance-verifiers.yml").read_text()
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.yml"
            duplicate = deepcopy(document)
            duplicate["verifiers"].append(deepcopy(duplicate["verifiers"][0]))
            path.write_text(yaml.safe_dump(duplicate, sort_keys=False))
            with self.assertRaises(PolicyError):
                load_verifier_policy(path=path)

            document["required_signoffs"] = 3
            path.write_text(yaml.safe_dump(document, sort_keys=False))
            with self.assertRaisesRegex(PolicyError, "below required_signoffs"):
                load_verifier_policy(path=path)


class IssueDecisionTests(unittest.TestCase):
    def test_registered_verifier_issue_becomes_record_attestation(self):
        parsed = parse_signoff_issue_body(issue()["body"])
        self.assertEqual(parsed["provenance_record"], RECORD)
        record_path, item = attestation_from_issue(issue(), policy())
        self.assertEqual(record_path, RECORD)
        self.assertEqual(item["verifier"], "sunt05")
        self.assertEqual(item["verifier_id"], 1802656)
        self.assertEqual(item["event"]["kind"], "issue")
        self.assertEqual(item["note"], "Checked against the cited source.")

    def test_non_verifier_is_rejected(self):
        for user in (
            {"login": "outsider", "id": 999, "type": "User"},
            {"login": "sunt05", "id": 999, "type": "User"},
        ):
            with self.subTest(user=user), self.assertRaisesRegex(
                AttestationError, "not an eligible verifier"
            ):
                fact_from_signoff_issue(issue(user=user), policy())

    def test_ci_rejects_stale_revision_and_sender_mismatch(self):
        sidecars = {RECORD: {"assessment": {"evidence_revision": EVIDENCE_REVISION}}}
        stale = issue(
            body=issue_body(**{"Evidence revision": "sha256:" + "9" * 64})
        )
        grouped, errors = collect_issue_attestations(
            [stale], policy(), sidecars, require_current=True
        )
        self.assertEqual(grouped, {})
        self.assertTrue(any("stale" in error for error in errors))

        event = {
            "repository": {"full_name": "UMEP-dev/SUEWS-database"},
            "issue": issue(),
            "sender": {"login": "outsider", "id": 999},
        }
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "event.json"
            event_path.write_text(json.dumps(event))
            with self.assertRaisesRegex(AttestationError, "sender"):
                validate_issue_event(event_path, policy(), sidecars)

    def test_withdrawal_requires_superseded_issue(self):
        with self.assertRaisesRegex(AttestationError, "must supersede"):
            parse_signoff_issue_body(issue_body(Decision="Withdrawn"))
        parsed = parse_signoff_issue_body(
            issue_body(Decision="Withdrawn", **{"Supersedes issue": "123"})
        )
        self.assertEqual(parsed["supersedes_event"], {"kind": "issue", "id": 123})


if __name__ == "__main__":
    unittest.main()
