#!/usr/bin/env python3
"""Prepare, resume and collect bounded provenance-audit runs.

The runner never performs scientific inference.  It freezes deterministic
packets for an agent, records the exact engine/query/source-access envelope of
each response, and materialises only schema-valid provenance candidates.  It
never edits database entries, publishes GitHub issues or creates verifier
attestations.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from check_db import load_all
from provenance import (
    PROVENANCE_FORMAT_VERSION,
    _dependency_keys,
    canonical_revision,
    check_provenance,
    evidence_revision,
    load_provenance_sidecars,
)


ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db"
RESPONSE_SCHEMA = ROOT / "schema" / "provenance-audit-response.schema.yml"
RUN_FORMAT_VERSION = "1.0"
RESPONSE_FORMAT_VERSION = "1.0"
MODES = ("evidence", "composition")
BLOCKING_CONCLUSIONS = {
    "contradicted",
    "correction_required",
    "unresolved",
    "source_inaccessible",
    "curation_required",
}

REVIEW_CONTRACTS = {
    "evidence": [
        "Read the original parameter publication; a registry key alone is not evidence.",
        "Separate parameter_source, input_data, compilation and validation roles.",
        "Check exact values, method, place, representativeness and identity.",
        "Give a precise table, figure, equation, section, page or text locator.",
        "Never infer a citation or change a value because it appears implausible.",
    ],
    "composition": [
        "Review why every direct component was selected and where it is mapped.",
        "Check place applicability, representativeness, completeness and compatible targets.",
        "Check slot, season and wet/dry mappings without re-verifying component values.",
        "Record every direct uses reference as role: component.",
        "Never infer a composition rationale from similarity to another archetype.",
    ],
}


class RunnerError(ValueError):
    """A run packet, response or candidate violates the bounded workflow."""


def _dump(value):
    return yaml.safe_dump(
        value, sort_keys=False, allow_unicode=True, width=100
    )


def _load(path):
    try:
        return yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise RunnerError(f"{path}: cannot read YAML: {exc}") from exc


def _write_if_changed(path, value):
    text = _dump(value)
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def _git_revision():
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _normalise_modes(mode):
    return list(MODES) if mode == "all" else [mode]


def _review_type(path):
    return "evidence" if path.startswith("records/") else "composition"


def _entry_paths(records, modes):
    wanted = set(modes)
    return [
        path
        for path in sorted(records)
        if _review_type(path) in wanted
    ]


def _component_paths(value):
    found = set()
    if isinstance(value, dict):
        for child in value.values():
            found.update(_component_paths(child))
    elif isinstance(value, str) and value.startswith(("records/", "archetypes/")):
        found.add(value)
    return found


def _packet_data(path, records, sources, places, existing_sidecars):
    entry = records[path]
    review_type = _review_type(path)
    source_keys = set()
    source = entry.get("source")
    if source not in (None, "unreferenced"):
        source_keys.add(source)
    source_entries = {
        key: sources[key] for key in sorted(source_keys) if key in sources
    }
    place_entries = {}
    if entry.get("place") in places:
        place_entries[entry["place"]] = places[entry["place"]]
    components = {}
    if review_type == "composition":
        components = {
            key: records[key]
            for key in sorted(_component_paths(entry.get("uses", {})))
            if key in records
        }
    previous = existing_sidecars.get(path)
    previous_review = None
    if previous:
        previous_review = {
            key: deepcopy(previous[key])
            for key in (
                "review_type",
                "provenance_format_version",
                "record_revision",
                "dependency_revisions",
                "assessment",
            )
            if key in previous
        }
    reviewed_input = {
        "review_type": review_type,
        "entry": entry,
        "sources": source_entries,
        "places": place_entries,
        "components": components,
        "previous_review": previous_review,
    }
    input_revision = canonical_revision(reviewed_input)
    packet = {
        "packet_format_version": RUN_FORMAT_VERSION,
        "provenance_record": path,
        "review_type": review_type,
        "input_revision": input_revision,
        "entry": entry,
        "registries": {
            "sources": source_entries,
            "places": place_entries,
        },
        "components": components,
        "previous_review": previous_review,
        "review_contract": REVIEW_CONTRACTS[review_type],
        "response_path": f"responses/{path}.yml",
    }
    return packet


def plan_run(modes, records=None, existing_sidecars=None):
    """Return the deterministic dry-run coverage summary without writing."""
    if records is None:
        records = load_all()[0]
    if existing_sidecars is None:
        existing_sidecars, errors = load_provenance_sidecars()
        if errors:
            raise RunnerError("; ".join(errors))
    selected = _entry_paths(records, modes)
    counts = {mode: 0 for mode in MODES}
    for path in selected:
        counts[_review_type(path)] += 1
    return {
        "run_format_version": RUN_FORMAT_VERSION,
        "modes": list(modes),
        "counts": counts,
        "total": len(selected),
        "existing_assessments": sum(path in existing_sidecars for path in selected),
        "entry_revision": canonical_revision(
            [
                {
                    "path": path,
                    "review_type": _review_type(path),
                    "record_revision": canonical_revision(records[path]),
                }
                for path in selected
            ]
        ),
    }


def _response_schema():
    schema = yaml.safe_load(RESPONSE_SCHEMA.read_text())
    Draft202012Validator.check_schema(schema)
    return schema


def _response_errors(response, manifest_entry, schema):
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = []
    for error in sorted(
        validator.iter_errors(response),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    ):
        path = ".".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"schema {path}: {error.message}")
    if errors or not isinstance(response, dict):
        return errors
    expected = {
        "provenance_record": manifest_entry["path"],
        "review_type": manifest_entry["review_type"],
        "input_revision": manifest_entry["input_revision"],
    }
    for key, value in expected.items():
        if response.get(key) != value:
            errors.append(
                f"{key} {response.get(key)!r} does not match packet {value!r}"
            )
    assessment = response.get("assessment", {})
    if assessment.get("status") == "verified":
        errors.append("an agent response cannot assert verified")
    if assessment.get("assessor", {}).get("kind") != "agent":
        errors.append("runner responses must identify an agent assessor")
    return errors


def _assessment_queue(assessment):
    conclusions = {
        item.get("conclusion")
        for item in assessment.get("findings", {}).values()
        if isinstance(item, dict)
    }
    if assessment.get("status") != "agent_assessed":
        return "problem"
    return "problem" if conclusions & BLOCKING_CONCLUSIONS else "review"


def _manifest_status(
    path, entry, out, schema, existing_sidecars, reassess_existing
):
    response_path = out / entry["response"]
    candidate_path = out / entry["candidate"]
    if response_path.exists():
        try:
            response = _load(response_path)
        except RunnerError:
            return "response_invalid", None
        errors = _response_errors(response, entry, schema)
        if any("input_revision" in error for error in errors):
            return "response_stale", response
        if errors:
            return "response_invalid", response
        if candidate_path.exists():
            try:
                candidate = _load(candidate_path)
            except RunnerError:
                return "candidate_invalid", response
            queue = _assessment_queue(candidate.get("assessment", {}))
            return f"candidate_{queue}", response
        return "response_ready", response
    if path in existing_sidecars and not reassess_existing:
        queue = _assessment_queue(existing_sidecars[path].get("assessment", {}))
        return f"existing_{queue}", None
    return "pending", None


def _build_manifest(
    out,
    modes,
    records,
    sources,
    places,
    existing_sidecars,
    repository_revision,
    reassess_existing,
):
    schema = _response_schema()
    entries = []
    source_groups = defaultdict(list)
    composition_groups = defaultdict(list)
    for path in _entry_paths(records, modes):
        packet = _packet_data(path, records, sources, places, existing_sidecars)
        packet_rel = f"packets/{path}.yml"
        response_rel = f"responses/{path}.yml"
        candidate_rel = f"candidates/db/provenance/{path}.yml"
        item = {
            "path": path,
            "review_type": packet["review_type"],
            "input_revision": packet["input_revision"],
            "packet": packet_rel,
            "response": response_rel,
            "candidate": candidate_rel,
        }
        status, response = _manifest_status(
            path, item, out, schema, existing_sidecars, reassess_existing
        )
        item["status"] = status
        if response:
            item["result"] = {
                "engine": response.get("engine"),
                "query": response.get("query"),
                "source_access": response.get("source_access"),
            }
        entries.append(item)
        if packet["review_type"] == "evidence":
            source_groups[records[path].get("source", "unreferenced")].append(path)
        else:
            group = path.split("/", 2)[1]
            composition_groups[group].append(path)
    manifest = {
        "run_format_version": RUN_FORMAT_VERSION,
        "repository_revision": repository_revision,
        "modes": list(modes),
        "reassess_existing": bool(reassess_existing),
        "run_revision": canonical_revision(
            [
                {
                    "path": item["path"],
                    "review_type": item["review_type"],
                    "input_revision": item["input_revision"],
                }
                for item in entries
            ]
        ),
        "counts": {
            mode: sum(item["review_type"] == mode for item in entries)
            for mode in MODES
        },
        "source_groups": dict(sorted(source_groups.items())),
        "composition_groups": dict(sorted(composition_groups.items())),
        "entries": entries,
    }
    return manifest


def prepare_run(
    out,
    modes,
    *,
    reassess_existing=False,
    records=None,
    sources=None,
    places=None,
    existing_sidecars=None,
    repository_revision=None,
):
    """Write deterministic packets and a resumable manifest."""
    if records is None or sources is None or places is None:
        records, sources, places = load_all()
    if existing_sidecars is None:
        existing_sidecars, errors = load_provenance_sidecars()
        if errors:
            raise RunnerError("; ".join(errors))
    repository_revision = repository_revision or _git_revision()
    out = Path(out)
    for path in _entry_paths(records, modes):
        packet = _packet_data(path, records, sources, places, existing_sidecars)
        _write_if_changed(out / "packets" / f"{path}.yml", packet)
    manifest = _build_manifest(
        out,
        modes,
        records,
        sources,
        places,
        existing_sidecars,
        repository_revision,
        reassess_existing,
    )
    _write_if_changed(out / "manifest.yml", manifest)
    issue_drafts = {}
    for item in manifest["entries"]:
        if item["status"] != "candidate_problem":
            continue
        candidate = _load(out / item["candidate"])
        issue_drafts[item["path"]] = _issue_draft(
            item["path"],
            records[item["path"]],
            candidate,
            repository_revision,
        )
    _write_queues(out, manifest, issue_drafts)
    return manifest


def _candidate_from_response(
    response, record, records, sources, places, existing_sidecar=None
):
    review_type = response["review_type"]
    assessment = deepcopy(response["assessment"])
    assessment["evidence_revision"] = "sha256:" + "0" * 64
    source_keys, place_keys, record_paths = _dependency_keys(
        record, assessment, review_type
    )
    unresolved = (
        [f"source {key!r}" for key in source_keys - set(sources)]
        + [f"place {key!r}" for key in place_keys - set(places)]
        + [f"entry {key!r}" for key in record_paths - set(records)]
    )
    if unresolved:
        raise RunnerError("unresolved dependencies: " + ", ".join(sorted(unresolved)))
    candidate = {
        "provenance_record": response["provenance_record"],
        "review_type": review_type,
        "provenance_format_version": PROVENANCE_FORMAT_VERSION,
        "record_revision": canonical_revision(record),
        "dependency_revisions": {
            "sources": {
                key: canonical_revision(sources[key]) for key in sorted(source_keys)
            },
            "places": {
                key: canonical_revision(places[key]) for key in sorted(place_keys)
            },
            "records": {
                key: canonical_revision(records[key]) for key in sorted(record_paths)
            },
        },
        "assessment": assessment,
        "verification": deepcopy(
            (existing_sidecar or {}).get("verification", {"attestations": []})
        ),
    }
    candidate["assessment"]["evidence_revision"] = evidence_revision(candidate)
    return candidate


def _problem_kind(findings):
    blocked = {
        scope: item
        for scope, item in findings.items()
        if item.get("conclusion") in BLOCKING_CONCLUSIONS
    }
    if "place" in blocked or "representativeness" in blocked:
        return "The place or representativeness is wrong"
    if "source" in blocked:
        return "The citation is wrong or missing"
    if "method" in blocked:
        return "The value is derived from other data, not measured in the cited source"
    if "values" in blocked:
        return "The value looks wrong"
    return "Something else"


def _issue_draft(path, record, candidate, repository_revision):
    findings = candidate["assessment"].get("findings", {})
    blocked = [
        (scope, item)
        for scope, item in findings.items()
        if item.get("conclusion") in BLOCKING_CONCLUSIONS
    ]
    details = []
    for scope, item in blocked:
        line = f"- **{scope}**: `{item.get('conclusion')}`"
        if item.get("note"):
            line += f" — {item['note']}"
        details.append(line)
    evidence = []
    for item in candidate["assessment"].get("evidence", []):
        subject = item.get("source") or item.get("record") or item.get("id")
        locators = ", ".join(
            locator.get("label", locator.get("kind", "locator"))
            for locator in item.get("locators", [])
        )
        evidence.append(f"- `{subject}`" + (f": {locators}" if locators else ""))
    body = "\n".join(
        [
            "### Record",
            "",
            path,
            "",
            "### Seen at",
            "",
            f"https://github.com/UMEP-dev/SUEWS-database/blob/{repository_revision}/db/{path}.yml",
            "",
            "### What kind of problem",
            "",
            _problem_kind(findings),
            "",
            "### What is wrong",
            "",
            *(details or ["The audit result requires human review."]),
            "",
            "### Evidence",
            "",
            *(evidence or ["_No response_"]),
            "",
        ]
    )
    return {
        "entry": path,
        "title": (
            f"[{'record' if path.startswith('records/') else 'composite'}] "
            f"{record.get('name') or path.rsplit('/', 1)[-1]}"
        ),
        "label": "data problem",
        "body": body,
    }


def _write_queues(out, manifest, issue_drafts):
    statuses = defaultdict(list)
    by_path = {item["path"]: item for item in manifest["entries"]}
    for item in manifest["entries"]:
        statuses[item["status"]].append(item["path"])
    queue_map = {
        "pending": {
            key: statuses[key]
            for key in ("pending", "response_invalid", "response_stale")
            if statuses[key]
        },
        "ready": {
            "response_ready": statuses["response_ready"]
        } if statuses["response_ready"] else {},
        "review": {
            key: statuses[key]
            for key in ("candidate_review", "existing_review")
            if statuses[key]
        },
        "problems": {
            key: statuses[key]
            for key in (
                "candidate_problem",
                "existing_problem",
                "candidate_invalid",
            )
            if statuses[key]
        },
    }
    for name, groups in queue_map.items():
        _write_if_changed(
            out / "queues" / f"{name}.yml",
            {
                "run_revision": manifest["run_revision"],
                "count": sum(len(items) for items in groups.values()),
                "groups": groups,
            },
        )
    issue_index = []
    for path, draft in sorted(issue_drafts.items()):
        body_file = f"issues/{path}.md"
        body_path = out / body_file
        body_path.parent.mkdir(parents=True, exist_ok=True)
        if not body_path.exists() or body_path.read_text() != draft["body"]:
            body_path.write_text(draft["body"])
        issue_index.append(
            {
                "entry": path,
                "title": draft["title"],
                "label": draft["label"],
                "body_file": body_file,
                "candidate": by_path[path]["candidate"],
            }
        )
    _write_if_changed(
        out / "queues" / "issue-drafts.yml",
        {
            "run_revision": manifest["run_revision"],
            "count": len(issue_index),
            "issues": issue_index,
        },
    )


def collect_run(
    run_dir,
    *,
    records=None,
    sources=None,
    places=None,
    existing_sidecars=None,
):
    """Validate responses and materialise candidates atomically."""
    run_dir = Path(run_dir)
    manifest = _load(run_dir / "manifest.yml")
    if not isinstance(manifest, dict) or manifest.get("run_format_version") != RUN_FORMAT_VERSION:
        raise RunnerError("manifest.yml is not a supported provenance run")
    if records is None or sources is None or places is None:
        records, sources, places = load_all()
    if existing_sidecars is None:
        existing_sidecars, errors = load_provenance_sidecars()
        if errors:
            raise RunnerError("; ".join(errors))
    candidate_base = run_dir / "candidates" / "db" / "provenance"
    prior_candidates, load_errors = load_provenance_sidecars(candidate_base)
    if load_errors:
        raise RunnerError("; ".join(load_errors))
    schema = _response_schema()
    built = {}
    errors = []
    by_path = {item["path"]: item for item in manifest["entries"]}
    for path, item in by_path.items():
        try:
            current_packet = _packet_data(
                path, records, sources, places, existing_sidecars
            )
            stored_packet = _load(run_dir / item["packet"])
        except (KeyError, RunnerError) as exc:
            errors.append(f"{path}: cannot validate packet: {exc}")
            continue
        if stored_packet != current_packet:
            errors.append(f"{path}: packet differs from current deterministic input")
            continue
        if item["input_revision"] != current_packet["input_revision"]:
            errors.append(f"{path}: manifest input_revision is stale")
            continue
        response_path = run_dir / item["response"]
        if not response_path.exists():
            continue
        try:
            response = _load(response_path)
        except RunnerError as exc:
            errors.append(str(exc))
            continue
        response_errors = _response_errors(response, item, schema)
        if response_errors:
            errors.extend(f"{path}: {error}" for error in response_errors)
            continue
        try:
            built[path] = _candidate_from_response(
                response,
                records[path],
                records,
                sources,
                places,
                existing_sidecars.get(path),
            )
        except (KeyError, RunnerError, TypeError, ValueError) as exc:
            errors.append(f"{path}: cannot build candidate: {exc}")
    combined_candidates = dict(prior_candidates)
    combined_candidates.update(built)
    combined_sidecars = dict(existing_sidecars)
    combined_sidecars.update(combined_candidates)
    errors.extend(check_provenance(records, sources, places, combined_sidecars))
    _write_if_changed(
        run_dir / "errors.yml",
        {"run_revision": manifest["run_revision"], "errors": sorted(set(errors))},
    )
    if errors:
        return manifest, sorted(set(errors))
    for path, candidate in sorted(built.items()):
        _write_if_changed(candidate_base / f"{path}.yml", candidate)
    refreshed = prepare_run(
        run_dir,
        manifest["modes"],
        reassess_existing=manifest.get("reassess_existing", False),
        records=records,
        sources=sources,
        places=places,
        existing_sidecars=existing_sidecars,
        repository_revision=manifest["repository_revision"],
    )
    return refreshed, []


def status_summary(manifest):
    statuses = defaultdict(int)
    for item in manifest.get("entries", []):
        statuses[item.get("status", "unknown")] += 1
    return {
        "run_revision": manifest.get("run_revision"),
        "modes": manifest.get("modes", []),
        "counts": manifest.get("counts", {}),
        "statuses": dict(sorted(statuses.items())),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="print deterministic coverage, write nothing")
    plan.add_argument("--mode", choices=(*MODES, "all"), default="all")

    prepare = sub.add_parser("prepare", help="write packets and resumable manifest")
    prepare.add_argument("--mode", choices=(*MODES, "all"), default="all")
    prepare.add_argument("--out", required=True, type=Path)
    prepare.add_argument("--reassess-existing", action="store_true")

    collect = sub.add_parser("collect", help="validate responses and write candidates")
    collect.add_argument("--run", required=True, type=Path)

    status = sub.add_parser("status", help="show manifest progress")
    status.add_argument("--run", required=True, type=Path)
    status.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            summary = plan_run(_normalise_modes(args.mode))
            print(json.dumps(summary, indent=2, sort_keys=True))
            return 0
        if args.command == "prepare":
            manifest = prepare_run(
                args.out,
                _normalise_modes(args.mode),
                reassess_existing=args.reassess_existing,
            )
            print(json.dumps(status_summary(manifest), indent=2, sort_keys=True))
            return 0
        if args.command == "collect":
            manifest, errors = collect_run(args.run)
            if errors:
                for error in errors:
                    print(error, file=sys.stderr)
                return 1
            print(json.dumps(status_summary(manifest), indent=2, sort_keys=True))
            return 0
        manifest = _load(args.run / "manifest.yml")
        summary = status_summary(manifest)
        if args.json:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            print(f"run {summary['run_revision']}")
            for key, value in summary["statuses"].items():
                print(f"  {key}: {value}")
        return 0
    except (
        OSError,
        RunnerError,
        TypeError,
        ValueError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"provenance runner: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
