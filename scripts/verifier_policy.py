"""Load the reviewed GitHub verifier policy used by provenance sign-off."""

from __future__ import annotations

from pathlib import Path

import rfc8785
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from provenance import canonical_revision


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / ".github" / "provenance-verifiers.yml"
SCHEMA_PATH = ROOT / "schema" / "provenance-verifier-policy.schema.yml"


class PolicyError(ValueError):
    """The reviewed verifier policy is malformed or ambiguous."""


def load_verifier_policy(path=POLICY_PATH, schema_path=SCHEMA_PATH):
    """Return a validated runtime policy with a content-derived revision."""
    try:
        document = yaml.safe_load(Path(path).read_text())
        schema = yaml.safe_load(Path(schema_path).read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(str(exc)) from exc

    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).iter_errors(document),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if errors:
        details = "; ".join(error.message for error in errors)
        raise PolicyError(details)

    handles = set()
    user_ids = set()
    verifiers = {}
    for item in document["verifiers"]:
        handle = item["github_handle"].casefold()
        user_id = item["github_user_id"]
        if handle in handles:
            raise PolicyError(f"duplicate GitHub handle: {item['github_handle']}")
        if user_id in user_ids:
            raise PolicyError(f"duplicate GitHub user ID: {user_id}")
        handles.add(handle)
        user_ids.add(user_id)
        verifiers[handle] = {
            "github_handle": item["github_handle"],
            "github_user_id": user_id,
            "scopes": tuple(item["scopes"]),
        }

    required = document["required_signoffs"]
    for scope in document["required_scopes"]:
        eligible = sum(scope in item["scopes"] for item in verifiers.values())
        if eligible < required:
            raise PolicyError(
                f"scope {scope!r} has {eligible} eligible verifier(s), "
                f"below required_signoffs={required}"
            )

    try:
        revision = canonical_revision(document)
    except (TypeError, ValueError, rfc8785.FloatDomainError) as exc:
        raise PolicyError(f"cannot fingerprint verifier policy: {exc}") from exc

    return {
        "revision": revision,
        "repository": document["repository"],
        "required_signoffs": document["required_signoffs"],
        "required_scopes": tuple(document["required_scopes"]),
        "verifiers": verifiers,
    }
