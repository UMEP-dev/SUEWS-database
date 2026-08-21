from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from github_attestation import (  # noqa: E402
    AttestationError,
    _record_rejection,
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
        "Reviewed entry": RECORD,
        "Review type": "Evidence",
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

            document["required_signoffs"] = len(document["verifiers"]) + 1
            path.write_text(yaml.safe_dump(document, sort_keys=False))
            with self.assertRaisesRegex(PolicyError, "below required_signoffs"):
                load_verifier_policy(path=path)


class IssueDecisionTests(unittest.TestCase):
    def test_registered_verifier_issue_becomes_record_attestation(self):
        parsed = parse_signoff_issue_body(issue()["body"])
        self.assertEqual(parsed["provenance_record"], RECORD)
        record_path, review_type, item = attestation_from_issue(issue(), policy())
        self.assertEqual(record_path, RECORD)
        self.assertEqual(review_type, "evidence")
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
            ) as caught:
                fact_from_signoff_issue(issue(user=user), policy())
            self.assertEqual(caught.exception.code, "unregistered_verifier")

        outsider = {"login": "outsider", "id": 999, "type": "User"}
        event = {
            "repository": {"full_name": "UMEP-dev/SUEWS-database"},
            "issue": issue(user=outsider),
            "sender": {"login": "outsider", "id": 999},
        }
        sidecars = {RECORD: {"assessment": {"evidence_revision": EVIDENCE_REVISION}}}
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "event.json"
            event_path.write_text(json.dumps(event))
            with self.assertRaises(AttestationError) as caught:
                validate_issue_event(event_path, policy(), sidecars)
        self.assertEqual(caught.exception.code, "unregistered_verifier")

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

    def test_composition_issue_is_distinct_from_evidence_review(self):
        composite = "archetypes/surfaces/bldgs/example"
        composition_issue = issue(
            body=issue_body(
                **{"Reviewed entry": composite, "Review type": "Composition"}
            )
        )
        sidecars = {
            composite: {
                "review_type": "composition",
                "assessment": {"evidence_revision": EVIDENCE_REVISION},
            }
        }
        grouped, errors = collect_issue_attestations(
            [composition_issue], policy(), sidecars, require_current=True
        )
        self.assertEqual(errors, [])
        self.assertEqual(grouped[composite][0]["event"]["id"], 201)

        wrong_layer = issue(
            body=issue_body(
                **{"Reviewed entry": composite, "Review type": "Evidence"}
            )
        )
        grouped, errors = collect_issue_attestations(
            [wrong_layer], policy(), sidecars, require_current=True
        )
        self.assertEqual(grouped, {})
        self.assertTrue(any("review type" in error for error in errors))

    def test_withdrawal_requires_superseded_issue(self):
        with self.assertRaisesRegex(AttestationError, "must supersede"):
            parse_signoff_issue_body(issue_body(Decision="Withdrawn"))
        parsed = parse_signoff_issue_body(
            issue_body(Decision="Withdrawn", **{"Supersedes issue": "123"})
        )
        self.assertEqual(parsed["supersedes_event"], {"kind": "issue", "id": 123})


class SignoffWorkflowTests(unittest.TestCase):
    def test_rejection_output_is_single_line_and_markdown_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "github-output"
            with patch.dict("os.environ", {"GITHUB_OUTPUT": str(output_path)}):
                _record_rejection("bad `field`\nsecond line", "invalid_signoff")
            self.assertEqual(
                output_path.read_text().splitlines(),
                [
                    "rejection_code=invalid_signoff",
                    "rejection_reason=bad 'field' second line",
                ],
            )

    def test_successful_validation_closes_open_issue(self):
        workflow = yaml.load(
            (ROOT / ".github" / "workflows" / "provenance-signoff.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        self.assertEqual(workflow["permissions"]["issues"], "write")

        steps = workflow["jobs"]["validate"]["steps"]
        names = [step.get("name") for step in steps]
        validation_index = names.index(
            "Validate verifier identity and decision revisions"
        )
        explanation_index = names.index("Explain rejected sign-off")
        rejection_index = names.index("Keep rejected sign-off open")
        close_index = names.index("Close accepted sign-off issue")
        self.assertEqual(steps[validation_index]["id"], "validate")
        self.assertEqual(steps[validation_index]["continue-on-error"], "true")
        self.assertGreater(explanation_index, validation_index)
        self.assertGreater(rejection_index, explanation_index)
        self.assertGreater(close_index, rejection_index)

        explanation_step = steps[explanation_index]
        self.assertIn("steps.validate.outcome == 'failure'", explanation_step["if"])
        self.assertIn("rejection_reason", str(explanation_step["env"]))
        self.assertIn("verifier-request.yml", explanation_step["run"])
        self.assertIn("gh issue comment", explanation_step["run"])

        close_step = steps[close_index]
        self.assertIn("github.event.issue.state == 'open'", close_step["if"])
        self.assertIn('gh issue close "$ISSUE_NUMBER"', close_step["run"])
        self.assertEqual(close_step["env"]["GH_TOKEN"], "${{ github.token }}")


if __name__ == "__main__":
    unittest.main()
