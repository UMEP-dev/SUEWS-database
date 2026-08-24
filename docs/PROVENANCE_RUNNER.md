# Provenance audit runner

`scripts/provenance_runner.py` is the deterministic boundary between the
database and an audit agent. It prepares immutable inputs, accepts explicit
agent responses, and materialises candidates only after the normal provenance
checker accepts them. It does not retrieve papers itself, change values,
publish issues, or create human sign-offs.

## Two passes

The runner keeps the two review layers separate:

- `evidence` covers all 848 atomic entries under `db/records/`;
- `composition` covers all 317 curated entries under `db/archetypes/`.

Check the current coverage without writing anything:

```sh
make audit-plan
# or one layer only
uv run --with pyyaml --with jsonschema --with rfc8785 --no-project \
  python scripts/provenance_runner.py plan --mode evidence
```

## Prepare a run

Run outputs belong outside the canonical database tree. The conventional
local directory is ignored by Git:

```sh
uv run --with pyyaml --with jsonschema --with rfc8785 --no-project \
  python scripts/provenance_runner.py prepare \
  --mode evidence --out provenance-runs/evidence-01
```

The output contains:

```text
manifest.yml                         deterministic entry list and progress
packets/<entry>.yml                  immutable input for one audit
responses/<entry>.yml                written by the audit agent
candidates/db/provenance/<entry>.yml schema-checked sidecar candidates
queues/pending.yml                   missing, invalid or stale responses
queues/ready.yml                     responses ready to collect
queues/review.yml                    candidates ready for human review
queues/problems.yml                  unresolved or corrective findings
queues/issue-drafts.yml              index of mend-record issue drafts
issues/<entry>.md                    structured issue body, never auto-posted
errors.yml                           deterministic validation errors
```

`prepare` is safe to rerun. A response is resumed only when its entry path,
review type and `input_revision` still match the packet. Changing an entry,
source, place, selected component or previous assessment marks the response
stale instead of silently reusing it. Existing repository sidecars count as
completed unless `--reassess-existing` is given.

Evidence packets are grouped by source in `manifest.yml`, so one retrieved
paper can be read once and applied carefully to its records. Composition
packets are grouped by archetype family and include every direct component.

## Agent response

The agent writes the path named by `response_path` in its packet. The response
is an operational envelope around an assessment:

```yaml
response_format_version: '1.0'
provenance_record: records/ohm/example
review_type: evidence
input_revision: sha256:<packet-revision>
engine:
  name: provenance-audit
  version: '1.0'
  model: <actual-model-if-known>
query: >-
  Check the stored values and provenance against the original publication.
source_access:
  - reference: grimmond1999
    outcome: accessed
    url: https://doi.org/...
    note: Full text read; Table 4 checked.
assessment:
  status: agent_assessed
  assessed_at: '2026-08-21T12:00:00Z'
  assessor: {kind: agent, name: provenance-audit, version: '1.0'}
  method: literature
  findings: ...
  evidence: ...
```

The response schema records the exact engine, query and source-access outcome.
The agent supplies no record, dependency or evidence revision and no
`verification` object. The runner derives those fields from current database
content and preserves any existing authenticated attestation history from the
repository rather than trusting agent output.

If a paper cannot be obtained, record the attempt honestly in `source_access`
and the assessment's `attempted_sources`; use `source_inaccessible` when the
missing source prevents a conclusion. Never replace the original source with a
similar paper.

## Collect and resume

```sh
uv run --with pyyaml --with jsonschema --with rfc8785 --no-project \
  python scripts/provenance_runner.py collect \
  --run provenance-runs/evidence-01

uv run --with pyyaml --with jsonschema --with rfc8785 --no-project \
  python scripts/provenance_runner.py status \
  --run provenance-runs/evidence-01
```

Collection is fail-closed and batch-atomic: invalid responses or provenance
graph errors produce `errors.yml` and no new candidates. Valid candidates have
their revisions recomputed and pass the same schema, dependency, derivation,
component and attestation checks as `make check`.

Actionable findings create issue **drafts** in the exact `mend-record` headed
format. They are not posted automatically: a maintainer first checks the title,
classification and evidence, then creates one GitHub issue per problem.

Candidate sidecars remain agent assessments. Human verification happens later
through the issue-based sign-off workflow; the runner has no operation that can
create an attestation or set `verified`.
