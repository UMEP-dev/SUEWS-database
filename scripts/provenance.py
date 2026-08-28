"""Offline validation and state derivation for provenance sidecars.

GitHub event authentication is deliberately outside this module.  Callers may
pass event identities that a trusted GitHub-aware service has authenticated;
without that input, an offline check can never derive ``verified``.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from pathlib import Path
import re

import rfc8785
import yaml
from jsonschema import Draft202012Validator, FormatChecker

from check_db import export_ref_leaf_paths


ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db"
SCHEMA_PATH = ROOT / "schema" / "provenance-assessment.schema.yml"
PROVENANCE_FORMAT_VERSION = "1.0"
GITHUB_REPOSITORY = "UMEP-dev/SUEWS-database"

REVIEWED_ASSESSMENT_FIELDS = (
    "status",
    "method",
    "findings",
    "evidence",
    "derivation",
    "attempted_sources",
    "scientific_note",
)
SIGNOFF_FINDINGS = {"supported", "not_applicable"}
EXTERNAL_METHODS = {"measured", "fitted", "literature"}
INTERNAL_METHODS = {"calculated", "assumed"}

EVENT_URLS = {
    "issue": re.compile(
        r"^https://github\.com/UMEP-dev/SUEWS-database/issues/(\d+)$"
    ),
}


def _json_compatible(value):
    """Project parsed YAML into JSON without losing integer map keys.

    Hour-indexed profiles use integer YAML keys. JSON object keys are strings,
    so the canonical projection uses their exact decimal spelling and rejects
    any collision with an already-string key.
    """
    if isinstance(value, dict):
        projected = {}
        for key, item in value.items():
            if isinstance(key, str):
                json_key = key
            elif isinstance(key, int) and not isinstance(key, bool):
                json_key = str(key)
            else:
                raise TypeError(
                    f"object key {key!r} is neither a string nor an integer"
                )
            if json_key in projected:
                raise TypeError(
                    f"object keys collide after JSON projection: {json_key!r}"
                )
            projected[json_key] = _json_compatible(item)
        return projected
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError(f"value {value!r} is not JSON-compatible")


def canonical_revision(value):
    """Return the RFC 8785 SHA-256 revision for a JSON-compatible value."""
    return "sha256:" + sha256(rfc8785.dumps(_json_compatible(value))).hexdigest()


def evidence_revision(sidecar):
    """Recompute the reviewed evidence projection documented in FORMAT.md."""
    assessment = sidecar["assessment"]
    projection = {
        "provenance_format_version": sidecar["provenance_format_version"],
        "record_revision": sidecar["record_revision"],
        "dependency_revisions": sidecar["dependency_revisions"],
        "assessment": {
            key: assessment[key]
            for key in REVIEWED_ASSESSMENT_FIELDS
            if key in assessment
        },
    }
    if "review_type" in sidecar:
        projection["review_type"] = sidecar["review_type"]
    return canonical_revision(projection)


def load_provenance_sidecars(base=DB / "provenance"):
    """Load sidecars keyed by their path below ``db/provenance`` sans suffix."""
    sidecars = {}
    errors = []
    if not base.exists():
        return sidecars, errors
    files = sorted(set(base.rglob("*.yml")) | set(base.rglob("*.yaml")))
    for fp in files:
        rel = fp.relative_to(base)
        key = str(rel.with_suffix(""))
        if fp.suffix != ".yml":
            errors.append(f"provenance/{rel}: sidecars must use the .yml suffix")
        try:
            parsed = yaml.safe_load(fp.read_text())
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"provenance/{rel}: cannot parse YAML: {exc}")
            continue
        if key in sidecars:
            errors.append(f"provenance/{rel}: duplicate sidecar path {key!r}")
            continue
        sidecars[key] = parsed
    return sidecars, errors


def _schema_errors(sidecar, schema):
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for error in sorted(
        validator.iter_errors(sidecar),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    ):
        path = ".".join(str(part) for part in error.absolute_path) or "<root>"
        yield f"schema {path}: {error.message}"


def _event_key(attestation):
    event = attestation.get("event", {})
    kind = event.get("kind")
    event_id = event.get("id")
    if kind not in EVENT_URLS or not isinstance(event_id, int):
        return None
    return kind, event_id


def _event_url_matches(attestation):
    event = attestation.get("event", {})
    pattern = EVENT_URLS.get(event.get("kind"))
    if pattern is None:
        return False
    match = pattern.fullmatch(str(event.get("url", "")))
    return bool(match and int(match.group(1)) == event.get("id"))


def _normalise_handle(handle):
    """Return a GitHub handle in the case-insensitive comparison form."""
    return handle.casefold() if isinstance(handle, str) else None


def _parameter_path_exists(record, path):
    """Whether a dotted ``parameters.*`` path resolves in the reviewed entry."""
    if not isinstance(path, str):
        return False
    parts = path.split(".")
    if len(parts) < 2 or parts[0] != "parameters":
        return False
    node = record
    for part in parts:
        if isinstance(node, dict):
            key = part
            if key not in node and part.isdigit() and int(part) in node:
                key = int(part)
            if key not in node:
                return False
            node = node[key]
        elif isinstance(node, list) and part.isdigit():
            index = int(part)
            if index >= len(node):
                return False
            node = node[index]
        else:
            return False
    return True


def _event_fact_matches(attestation, fact, provenance_record):
    """Match an attestation to facts authenticated by a GitHub-aware caller."""
    if not isinstance(fact, dict):
        return False
    event = attestation.get("event", {})
    return (
        _normalise_handle(fact.get("author"))
        == _normalise_handle(attestation.get("verifier"))
        and fact.get("author_id") == attestation.get("verifier_id")
        and fact.get("signed_at") == attestation.get("signed_at")
        and fact.get("url") == event.get("url")
        and fact.get("repository") == GITHUB_REPOSITORY
        and fact.get("decision") == attestation.get("decision")
        and fact.get("provenance_record") == provenance_record
        and fact.get("evidence_revision")
        == attestation.get("evidence_revision")
        and fact.get("verifier_policy_revision")
        == attestation.get("verifier_policy_revision")
        and fact.get("scope") == attestation.get("scope")
        and fact.get("supersedes_event") == attestation.get("supersedes_event")
    )


def _evidence_covers_path(item, path):
    scopes = item.get("parameter_paths")
    if not scopes:
        return True
    return any(path == scope or path.startswith(scope + ".") for scope in scopes)


def _effective_leaf_metadata(record, assessment):
    """Yield each exported path with its effective canonical source and method."""
    overrides = record.get("parameter_provenance", {})
    for path in export_ref_leaf_paths(record):
        override = overrides.get(path, {})
        source = override.get("source", record.get("source"))
        method = override.get(
            "method", record.get("method", assessment.get("method"))
        )
        yield path, source, method


def _parameter_source_aligned(record, assessment):
    """Whether every exported leaf has one honest parameter-source claim."""
    parameter_sources = [
        item
        for item in assessment.get("evidence", [])
        if item.get("role") == "parameter_source"
    ]
    if not record.get("parameter_provenance"):
        source = record.get("source")
        method = record.get("method", assessment.get("method"))
        if source == "unreferenced":
            return method in INTERNAL_METHODS and not parameter_sources
        return method in EXTERNAL_METHODS | INTERNAL_METHODS and any(
            item.get("source") == source and not item.get("parameter_paths")
            for item in parameter_sources
        )
    for path, source, method in _effective_leaf_metadata(record, assessment):
        covering = [
            item for item in parameter_sources if _evidence_covers_path(item, path)
        ]
        if method not in EXTERNAL_METHODS | INTERNAL_METHODS:
            return False
        if source == "unreferenced":
            if method not in INTERNAL_METHODS or covering:
                return False
        elif not covering or any(item.get("source") != source for item in covering):
            return False
    return True


def _record_has_published_parameter_source(record, assessment):
    return any(
        source not in (None, "unreferenced")
        for _, source, _ in _effective_leaf_metadata(record, assessment)
    )


def _review_type(sidecar):
    """Return the explicit review layer; old sidecars are evidence reviews."""
    return sidecar.get("review_type", "evidence")


def signoff_eligible(sidecar, record):
    """Whether human attestations are allowed to affect the record state."""
    if not isinstance(sidecar, dict) or not isinstance(record, dict):
        return False
    assessment = sidecar.get("assessment", {})
    if assessment.get("status") != "agent_assessed":
        return False
    findings = assessment.get("findings", {})
    if not findings or any(
        finding.get("conclusion") not in SIGNOFF_FINDINGS
        for finding in findings.values()
    ):
        return False
    for scope in ("urban_setting", "applicable_scale"):
        if (
            record.get(scope) is not None
            and findings.get(scope, {}).get("conclusion") != "supported"
        ):
            return False
    review_type = _review_type(sidecar)
    if findings.get("method", {}).get("conclusion") != "supported":
        return False
    if review_type == "evidence":
        values_conclusion = findings.get("values", {}).get("conclusion")
        values_supported = values_conclusion == "supported" or (
            # A deliberate no-active-parameter adapter row is attestable
            # as an adapter: values cannot be supported because there are
            # no active values to support.
            values_conclusion == "not_applicable"
            and not record.get("parameters")
        )
        if not values_supported:
            return False
    if review_type == "evidence" and _record_has_published_parameter_source(
        record, assessment
    ) and findings.get("source", {}).get("conclusion") != "supported":
        return False
    try:
        revisions_are_current = (
            sidecar.get("record_revision") == canonical_revision(record)
            and assessment.get("evidence_revision") == evidence_revision(sidecar)
        )
    except (KeyError, TypeError, ValueError, rfc8785.FloatDomainError):
        return False
    if not revisions_are_current:
        return False
    if review_type == "composition":
        return assessment.get("method") == "assembled"
    return _parameter_source_aligned(record, assessment)


def derive_verification_state(
    sidecar,
    record,
    *,
    policy=None,
    authenticated_events=None,
    records=None,
    sources=None,
    places=None,
    sidecars=None,
):
    """Derive the record state from trusted inputs.

    ``authenticated_events`` maps ``(kind, id)`` pairs to event facts verified
    through GitHub by infrastructure outside the offline checker.  Each fact
    supplies the event identity, signed decision payload, author, timestamp,
    URL and repository; every field must match the attestation. ``policy`` has
    a revision, a positive
    ``required_signoffs`` count, non-empty ``required_scopes``, and a
    ``verifiers`` mapping from GitHub handles to immutable numeric user IDs and
    allowed scopes. Missing trust inputs always yield ``awaiting_signoff`` for
    an otherwise eligible assessment. Current record, source and place
    registries plus the complete sidecar snapshot are also required. The
    snapshot must pass the same schema, reference, revision and graph checks
    used by ``make check``; stale or checker-invalid evidence can never produce
    ``verified``.
    """
    if sidecar is None:
        return "unaudited"
    if not isinstance(sidecar, dict):
        return "unresolved"
    assessment = sidecar.get("assessment", {})
    provenance_record = sidecar.get("provenance_record")
    if (
        not isinstance(sidecars, dict)
        or sidecars.get(provenance_record) != sidecar
        or not all(
            isinstance(value, dict) for value in (records, sources, places)
        )
        or check_provenance(records, sources, places, sidecars)
    ):
        return "unresolved"
    status = assessment.get("status")
    if status in {"unresolved", "source_inaccessible", "curation_required"}:
        return status
    if status != "agent_assessed":
        return "unresolved"
    if not signoff_eligible(sidecar, record):
        return "agent_assessed"
    if not _dependencies_are_current(
        sidecar, record, records, sources, places
    ):
        return "agent_assessed"
    if not policy:
        return "awaiting_signoff"
    policy_revision = policy.get("revision")
    required = policy.get("required_signoffs")
    required_scopes = policy.get("required_scopes")
    verifiers = policy.get("verifiers", {})
    if (
        not policy_revision
        or not isinstance(required, int)
        or isinstance(required, bool)
        or required < 1
        or not isinstance(required_scopes, (list, tuple, set, frozenset))
        or not required_scopes
        or not all(isinstance(scope, str) for scope in required_scopes)
        or not isinstance(verifiers, dict)
    ):
        return "awaiting_signoff"

    required_scopes = set(required_scopes)
    allowed_by_verifier = {}
    for handle, verifier in verifiers.items():
        if not isinstance(verifier, dict):
            continue
        scopes = verifier.get("scopes")
        user_id = verifier.get("github_user_id")
        if (
            isinstance(user_id, int)
            and not isinstance(user_id, bool)
            and isinstance(scopes, (list, tuple, set, frozenset))
            and all(isinstance(scope, str) for scope in scopes)
        ):
            allowed_by_verifier[_normalise_handle(handle)] = {
                "github_user_id": user_id,
                "scopes": set(scopes),
            }
    authenticated_events = authenticated_events or {}
    attestations = sidecar.get("verification", {}).get("attestations", [])
    event_keys = [_event_key(item) for item in attestations]
    if (
        any(key is None for key in event_keys)
        or len(set(event_keys)) != len(event_keys)
        or _attestation_supersession_errors(attestations)
    ):
        return "awaiting_signoff"
    event_key_set = set(event_keys)
    for attestation, key in zip(attestations, event_keys):
        target = attestation.get("supersedes_event")
        if target:
            target_key = (target.get("kind"), target.get("id"))
            if target_key == key or target_key not in event_key_set:
                return "awaiting_signoff"

    current = {}
    for attestation in attestations:
        key = _event_key(attestation)
        if key is None:
            continue
        if not _event_url_matches(attestation):
            continue
        fact = authenticated_events.get(key)
        if not _event_fact_matches(
            attestation, fact, sidecar.get("provenance_record")
        ):
            continue
        if attestation.get("evidence_revision") != assessment.get(
            "evidence_revision"
        ):
            continue
        if attestation.get("verifier_policy_revision") != policy_revision:
            continue
        verifier = _normalise_handle(attestation.get("verifier"))
        verifier_policy = allowed_by_verifier.get(verifier, {})
        scope = attestation.get("scope")
        if (
            scope not in required_scopes
            or attestation.get("verifier_id")
            != verifier_policy.get("github_user_id")
            or scope not in verifier_policy.get("scopes", set())
        ):
            continue
        current[key] = attestation

    superseded = {
        (target.get("kind"), target.get("id"))
        for attestation in current.values()
        if (target := attestation.get("supersedes_event"))
    }
    effective = {
        key: attestation
        for key, attestation in current.items()
        if key not in superseded and attestation.get("decision") != "withdrawn"
    }

    decisions = {item.get("decision") for item in effective.values()}
    if decisions & {"changes_requested", "unresolved", "curation_required"}:
        return "awaiting_signoff"
    signed = {
        item.get("verifier_id")
        for item in effective.values()
        if item.get("decision") == "verified"
    }
    covered_scopes = {
        item.get("scope")
        for item in effective.values()
        if item.get("decision") == "verified"
    }
    if len(signed) >= required and required_scopes <= covered_scopes:
        return "verified"
    return "awaiting_signoff"


def _iter_entry_refs(value):
    """Yield record/archetype references nested in a composition mapping."""
    if isinstance(value, dict):
        for child in value.values():
            yield from _iter_entry_refs(child)
    elif isinstance(value, str) and value.startswith(("records/", "archetypes/")):
        yield value


def _dependency_keys(record, assessment, review_type="evidence"):
    source_keys = set()
    if record.get("source") not in (None, "unreferenced"):
        source_keys.add(record["source"])
    place_keys = {record["place"]} if record.get("place") else set()
    for override in record.get("parameter_provenance", {}).values():
        if override.get("source") not in (None, "unreferenced"):
            source_keys.add(override["source"])
        if override.get("place"):
            place_keys.add(override["place"])
    record_paths = set()
    for item in assessment.get("evidence", []):
        if "source" in item:
            source_keys.add(item["source"])
        if "record" in item:
            record_paths.add(item["record"])
    for attempt in assessment.get("attempted_sources", []):
        if "source" in attempt:
            source_keys.add(attempt["source"])
    for finding in assessment.get("findings", {}).values():
        record_paths.update(finding.get("related_records", []))
    if review_type == "composition":
        record_paths.update(_iter_entry_refs(record.get("uses", {})))
    return source_keys, place_keys, record_paths


def _dependencies_are_current(sidecar, record, records, sources, places):
    """Whether every declared scientific dependency matches current content."""
    if not all(isinstance(value, dict) for value in (records, sources, places)):
        return False
    if records.get(sidecar.get("provenance_record")) != record:
        return False
    assessment = sidecar.get("assessment", {})
    source_keys, place_keys, record_paths = _dependency_keys(
        record, assessment, _review_type(sidecar)
    )
    dependencies = sidecar.get("dependency_revisions", {})
    sections = (
        (dependencies.get("sources", {}), source_keys, sources),
        (dependencies.get("places", {}), place_keys, places),
        (dependencies.get("records", {}), record_paths, records),
    )
    for actual, expected, registry in sections:
        if set(actual) != expected or not expected <= set(registry):
            return False
        try:
            if any(actual[key] != canonical_revision(registry[key]) for key in expected):
                return False
        except (TypeError, ValueError, rfc8785.FloatDomainError):
            return False
    return True


def _check_dependency_section(label, actual, expected, registry):
    errors = []
    actual_keys = set(actual)
    if actual_keys != expected:
        missing = sorted(expected - actual_keys)
        extra = sorted(actual_keys - expected)
        if missing:
            errors.append(f"{label}: missing dependency revisions {missing}")
        if extra:
            errors.append(f"{label}: unexplained dependency revisions {extra}")
    for key in sorted(actual_keys & set(registry)):
        try:
            wanted = canonical_revision(registry[key])
        except (TypeError, ValueError, rfc8785.FloatDomainError) as exc:
            errors.append(f"{label}.{key}: cannot fingerprint dependency: {exc}")
            continue
        if actual[key] != wanted:
            errors.append(f"{label}.{key}: dependency revision is stale")
    return errors


def _find_cycles(graph):
    errors = []
    state = {}
    stack = []

    def visit(node):
        state[node] = 1
        stack.append(node)
        for target in sorted(graph.get(node, ())):
            if target not in graph:
                continue
            if state.get(target) == 1:
                start = stack.index(target)
                cycle = stack[start:] + [target]
                errors.append("derivation cycle: " + " -> ".join(cycle))
            elif state.get(target) is None:
                visit(target)
        stack.pop()
        state[node] = 2

    for node in sorted(graph):
        if state.get(node) is None:
            visit(node)
    return errors


def _event_label(key):
    return f"{key[0]}:{key[1]}"


def _parse_rfc3339(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _attestation_supersession_errors(attestations):
    """Validate immutable attestation replacement links."""
    errors = []
    by_key = {
        key: item
        for item in attestations
        if (key := _event_key(item)) is not None
    }
    graph = {}
    for key, item in by_key.items():
        supersedes = item.get("supersedes_event")
        if not supersedes:
            continue
        target = (supersedes.get("kind"), supersedes.get("id"))
        graph[key] = target
        if target not in by_key:
            continue
        previous = by_key[target]
        if (
            _normalise_handle(previous.get("verifier"))
            != _normalise_handle(item.get("verifier"))
            or previous.get("verifier_id") != item.get("verifier_id")
        ):
            errors.append(
                f"attestation {_event_label(key)} cannot supersede another verifier"
            )
        if previous.get("scope") != item.get("scope"):
            errors.append(
                f"attestation {_event_label(key)} cannot change review scope"
            )
        try:
            is_later = _parse_rfc3339(item["signed_at"]) > _parse_rfc3339(
                previous["signed_at"]
            )
        except (KeyError, TypeError, ValueError):
            # Date-time shape is reported by the JSON Schema validator.
            is_later = True
        if not is_later:
            errors.append(
                f"attestation {_event_label(key)} must be later than the event "
                "it supersedes"
            )

    state = {}
    stack = []

    def visit(node):
        state[node] = 1
        stack.append(node)
        target = graph.get(node)
        if target in graph:
            if state.get(target) == 1:
                start = stack.index(target)
                cycle = stack[start:] + [target]
                errors.append(
                    "attestation supersession cycle: "
                    + " -> ".join(_event_label(key) for key in cycle)
                )
            elif state.get(target) is None:
                visit(target)
        stack.pop()
        state[node] = 2

    for node in graph:
        if state.get(node) is None:
            visit(node)
    return errors


def check_provenance(records, sources, places, sidecars, schema=None):
    """Return offline structural and semantic errors for provenance sidecars."""
    if schema is None:
        schema = yaml.safe_load(SCHEMA_PATH.read_text())
    Draft202012Validator.check_schema(schema)
    errors = []
    derivations = {}
    attestation_events = {}

    for sidecar_path, sidecar in sorted(sidecars.items()):
        label = f"provenance/{sidecar_path}"
        if not isinstance(sidecar, dict):
            errors.append(f"{label}: sidecar must be a mapping")
            continue
        schema_errors = list(_schema_errors(sidecar, schema))
        errors.extend(f"{label}: {message}" for message in schema_errors)
        if schema_errors:
            # Semantic checks assume the schema-established container types.
            # A malformed fixture must be reported, never crash the checker.
            continue

        declared = sidecar.get("provenance_record")
        if declared != sidecar_path:
            errors.append(
                f"{label}: provenance_record {declared!r} != file location"
            )
        record = records.get(declared)
        review_type = _review_type(sidecar)
        expected_prefix = "archetypes/" if review_type == "composition" else "records/"
        if not isinstance(record, dict) or not str(declared).startswith(expected_prefix):
            errors.append(
                f"{label}: unresolved {review_type} entry {declared!r}"
            )
            continue
        assessment = sidecar.get("assessment")
        if not isinstance(assessment, dict):
            continue

        try:
            wanted_record_revision = canonical_revision(record)
        except (TypeError, ValueError, rfc8785.FloatDomainError) as exc:
            errors.append(f"{label}: cannot fingerprint evidence record: {exc}")
            continue
        if sidecar.get("record_revision") != wanted_record_revision:
            errors.append(f"{label}: record_revision is stale")

        evidence_by_id = {}
        input_records = set()
        documented_components = set()
        for item in assessment.get("evidence", []):
            evidence_id = item.get("id")
            if evidence_id in evidence_by_id:
                errors.append(f"{label}: duplicate evidence id {evidence_id!r}")
            evidence_by_id[evidence_id] = item
            source_key = item.get("source")
            if source_key is not None and source_key not in sources:
                errors.append(f"{label}: evidence source {source_key!r} unresolved")
            record_ref = item.get("record")
            if record_ref is not None:
                if record_ref not in records:
                    errors.append(f"{label}: evidence record {record_ref!r} unresolved")
                if record_ref == declared:
                    errors.append(f"{label}: evidence record self-reference")
                if item.get("role") == "input":
                    input_records.add(record_ref)
                if item.get("role") == "component":
                    documented_components.add(record_ref)
            for parameter_path in item.get("parameter_paths", []):
                if not _parameter_path_exists(record, parameter_path):
                    errors.append(
                        f"{label}: evidence {evidence_id!r} parameter path "
                        f"{parameter_path!r} is not present in the reviewed entry"
                    )
        if review_type == "composition":
            component_refs = set(_iter_entry_refs(record.get("uses", {})))
            if documented_components != component_refs:
                missing = sorted(component_refs - documented_components)
                extra = sorted(documented_components - component_refs)
                if missing:
                    errors.append(
                        f"{label}: undocumented composition components {missing}"
                    )
                if extra:
                    errors.append(
                        f"{label}: component evidence not used by composite {extra}"
                    )
        derivations[declared] = input_records
        derivation = assessment.get("derivation", {})
        derivation_kind = derivation.get("kind")
        external_input_ids = set()
        for external_input in derivation.get("external_inputs", []):
            input_id = external_input.get("id")
            if input_id in external_input_ids:
                errors.append(
                    f"{label}: duplicate external derivation input {input_id!r}"
                )
            external_input_ids.add(input_id)
            evidence_id = external_input.get("evidence_id")
            evidence_item = evidence_by_id.get(evidence_id)
            if evidence_item is None:
                errors.append(
                    f"{label}: external input {input_id!r} references unknown "
                    f"evidence id {evidence_id!r}"
                )
            elif (
                evidence_item.get("source") is None
                or evidence_item.get("role") != "input_data"
            ):
                errors.append(
                    f"{label}: external input {input_id!r} must reference "
                    "source evidence with role 'input_data'"
                )
        if derivation_kind in {"arithmetic_mean", "weighted_mean"}:
            if len(input_records) + len(external_input_ids) < 2:
                errors.append(
                    f"{label}: {derivation_kind} requires two distinct inputs"
                )

        findings = assessment.get("findings", {})
        for optional_scope in ("urban_setting", "applicable_scale"):
            if (
                record.get(optional_scope) is not None
                and optional_scope not in findings
            ):
                errors.append(
                    f"{label}: entry declares {optional_scope} but assessment "
                    f"has no {optional_scope} finding"
                )
            elif (
                record.get(optional_scope) is not None
                and findings[optional_scope].get("conclusion") == "not_applicable"
            ):
                errors.append(
                    f"{label}: declared {optional_scope} cannot be not_applicable"
                )
        for scope, finding in findings.items():
            for evidence_id in finding.get("evidence_ids", []):
                if evidence_id not in evidence_by_id:
                    errors.append(
                        f"{label}: finding {scope!r} references unknown "
                        f"evidence id {evidence_id!r}"
                    )
            for record_ref in finding.get("related_records", []):
                if record_ref not in records:
                    errors.append(
                        f"{label}: finding {scope!r} related record "
                        f"{record_ref!r} unresolved"
                    )
                if record_ref == declared:
                    errors.append(
                        f"{label}: finding {scope!r} related record self-reference"
                    )

        method = assessment.get("method")
        if (
            derivation_kind
            in {"arithmetic_mean", "weighted_mean", "scaled", "other"}
            and method != "calculated"
        ):
            errors.append(
                f"{label}: internal derivation {derivation_kind!r} requires "
                "method 'calculated'"
            )
        if derivation_kind == "regression" and method != "fitted":
            errors.append(f"{label}: regression derivation requires method 'fitted'")
        if review_type == "evidence" and method in EXTERNAL_METHODS | INTERNAL_METHODS:
            if _record_has_published_parameter_source(record, assessment):
                for scope in ("source", "values", "method"):
                    finding = findings.get(scope, {})
                    if finding.get("conclusion") in {
                        "supported",
                        "contradicted",
                        "correction_required",
                    } and not finding.get("evidence_ids"):
                        errors.append(
                            f"{label}: definitive {scope} finding requires "
                            "evidence_ids"
                        )
            if not _parameter_source_aligned(record, assessment):
                if findings.get("source", {}).get("conclusion") != (
                    "correction_required"
                ):
                    errors.append(
                        f"{label}: parameter_source does not match effective "
                        "parameter sources; source finding must be "
                        "correction_required"
                    )
        record_method = record.get("method") if review_type == "evidence" else None
        if record_method and method and record_method != method:
            if findings.get("method", {}).get("conclusion") not in {
                "contradicted",
                "correction_required",
            }:
                errors.append(
                    f"{label}: assessment method {method!r} differs from record.method "
                    f"{record_method!r} without a blocking method finding"
                )

        dependencies = sidecar.get("dependency_revisions", {})
        source_keys, place_keys, record_paths = _dependency_keys(
            record, assessment, review_type
        )
        for key in sorted(source_keys - set(sources)):
            errors.append(f"{label}: dependency source {key!r} unresolved")
        for key in sorted(place_keys - set(places)):
            errors.append(f"{label}: dependency place {key!r} unresolved")
        for key in sorted(record_paths - set(records)):
            errors.append(f"{label}: dependency record {key!r} unresolved")
        errors.extend(
            f"{label}: {message}"
            for message in _check_dependency_section(
                "sources",
                dependencies.get("sources", {}),
                source_keys,
                sources,
            )
        )
        errors.extend(
            f"{label}: {message}"
            for message in _check_dependency_section(
                "places",
                dependencies.get("places", {}),
                place_keys,
                places,
            )
        )
        errors.extend(
            f"{label}: {message}"
            for message in _check_dependency_section(
                "records",
                dependencies.get("records", {}),
                record_paths,
                records,
            )
        )

        try:
            wanted_evidence_revision = evidence_revision(sidecar)
        except (KeyError, TypeError, ValueError, rfc8785.FloatDomainError) as exc:
            errors.append(f"{label}: cannot fingerprint assessment: {exc}")
        else:
            if assessment.get("evidence_revision") != wanted_evidence_revision:
                errors.append(f"{label}: evidence_revision is stale")

        attestations = sidecar.get("verification", {}).get("attestations", [])
        event_keys = {_event_key(item) for item in attestations}
        event_keys.discard(None)
        seen = set()
        for attestation in attestations:
            key = _event_key(attestation)
            if key in seen:
                errors.append(f"{label}: duplicate attestation event {key!r}")
            if key is not None:
                seen.add(key)
                previous_sidecar = attestation_events.get(key)
                if previous_sidecar is not None and previous_sidecar != declared:
                    errors.append(
                        f"{label}: attestation event {key!r} is already used by "
                        f"provenance/{previous_sidecar}"
                    )
                else:
                    attestation_events[key] = declared
            if not _event_url_matches(attestation):
                errors.append(f"{label}: attestation event ID does not match URL")
            supersedes = attestation.get("supersedes_event")
            if supersedes:
                target = (supersedes.get("kind"), supersedes.get("id"))
                if target == key:
                    errors.append(f"{label}: attestation cannot supersede itself")
                elif target not in event_keys:
                    errors.append(
                        f"{label}: superseded attestation event {target!r} not found"
                    )
        errors.extend(
            f"{label}: {message}"
            for message in _attestation_supersession_errors(attestations)
        )

    errors.extend(_find_cycles(derivations))
    return errors
