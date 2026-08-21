# The record format

The database stores parameter values as records: one YAML file per
source-coherent set of values, named by supy's canonical parameter paths so
that a record exports mechanically into a modern SUEWS YAML configuration.

Three layers make up the database.

- **Evidence** (`db/records/`) — values as they were measured, fitted or
  published: one file per set that shares a source, a place and a surface.
  Values that were determined together stay together (a phenology record
  carries the degree-day totals *and* the base temperatures they were
  accumulated against), which is what makes a record safe to reuse whole.
- **Assembly** (`db/archetypes/`) — curated combinations: a complete surface
  description (`archetypes/surfaces/`), a snow parameter set
  (`archetypes/snow/`), a building typology (`archetypes/typologies/`), or a
  regional/country default set (`archetypes/regions/`,
  `archetypes/countries/`). Archetypes carry `uses:` references to evidence
  records rather than copies of their values, so provenance survives
  assembly.
- **Provenance review** (`db/provenance/`) — sidecars mirror stable evidence or
  composite paths. Evidence review checks values and sources; composition
  review checks component selection and mapping. Both keep agent assessment
  separate from GitHub-backed human verifier attestations.

Two registries resolve the short names records use.

- `db/sources.yml` — citation keys (`ward2016`, `jarvi2014`) with author,
  year, title, journal and DOI. `unreferenced` marks legacy values that
  carried no citation.
- `db/places.yml` — place slugs (`helsinki`, `se-england`) for every
  location a record claims. `schema/origins_map.yml` records how each raw
  legacy `Origin` string was resolved, with a confidence flag.

## An evidence record

```yaml
record: records/surfaces/grass/helsinki--jarvi2014--phenology
schema_version: '2026.5'          # supy schema the parameter names target
target: land_cover.grass          # where the fragment belongs in a config
name: Grass
place: helsinki                   # -> db/places.yml
origin: Helsinki                  # the raw legacy string, kept verbatim
representativeness: city          # site | city | regional | generic
source: jarvi2014                 # -> db/sources.yml
legacy_id: 35240003               # the row this came from, pre-migration
parameters:                       # supy canonical names under `target`
  lai:
    base_temperature: 5
    base_temperature_senescence: 10
    gdd_full: 300
    sdd_full: -450
```

Envelope fields:

- `record` — the path, always equal to the file's location under `db/`
  without `.yml`. `make check` enforces this.
- `schema_version` — the supy schema the parameter names were verified
  against. When supy renames fields, records migrate with supy's own
  schema-migration tooling and this pin moves.
- `target` — the supy path the `parameters:` fragment validates against:
  `land_cover.<surface>` (one of paved, bldgs, evetr, dectr, grass, bsoil,
  water, or `common` for surface-agnostic sets such as soils), `snow`,
  `conductance`, `irrigation`, `anthropogenic_emissions`,
  `profile.<kind>`, or the repo-local types `ohm_coefficients`, `material`,
  `construction`.
- `place` + `representativeness` — where the values are from and how far
  they travel. A rooftop measurement (`site`) and a value offered as typical
  of a region (`regional`) are no longer indistinguishable.
- `method` (optional) — measured | fitted | literature | calculated | assumed.
  Not recoverable for migrated records; fill it in for new ones.
- `legacy:` (optional) — columns of the migrated row that have no home in
  the current supy model (documented per column in
  `schema/table_mapping.yml`), and `-999` placeholder cells. Kept verbatim
  so nothing the legacy database asserted is lost.
- `context:` inside `parameters:` — values that condition the set without
  being model inputs themselves (e.g. which QF formulation an emission set
  was fitted for, which conductance model a g-set belongs to).

## Provenance assessment and human verification

The record's `source` remains the backwards-compatible citation that travels
with its values into a SUEWS configuration. Under this workflow it means **the
publication that states or derives the stored parameter values**. An assessment
that finds a different meaning in an existing record raises a record-specific
data problem; it does not silently redefine the citation. `source` must not be
changed to the publication that supplied underlying observations when that
publication did not publish the parameter itself.

An internally calculated or explicitly assumed record may have no external
parameter publication and keep `source: unreferenced`. Its sidecar makes that
absence precise by recording the method, input records or assumption, and the
human decision that accepted it; it does not manufacture a publication.

Full provenance and its review state live in a sidecar under `db/provenance/`
that mirrors the reviewed entry path. For example, the assessment for
`db/records/ohm/example.yml` is `db/provenance/records/ohm/example.yml`, while
an archetype review mirrors `db/provenance/archetypes/...`. Keeping candidate
assessments outside the entry lets an agent prepare a repository-wide review
queue without presenting its findings as accepted data or changing exports.

`schema/provenance-assessment.schema.yml` is the machine-readable sidecar
schema. A sidecar contains:

- `provenance_record` — the stable evidence-record or archetype path it assesses;
- `review_type` — `evidence` or `composition` (omitted legacy sidecars mean
  `evidence`);
- `provenance_format_version` — the sidecar format version, independent of the
  record's supy `schema_version`;
- `record_revision` — a SHA-256 fingerprint of the complete parsed record;
- `dependency_revisions` — fingerprints of every source, place and record
  entry on which the assessment relies;
- `assessment` — structured findings, evidence locators, derivation and the
  agent or human assessor;
- `verification` — immutable GitHub-backed attestation events only.

Verifier eligibility, required review scopes and sign-off thresholds are not
stored in this agent-writable sidecar. They come from the centrally maintained
verifier policy described below. This prevents an assessment producer from
lowering its own verification threshold.

The method vocabulary extends the optional record field to:

| Method | Meaning |
|---|---|
| `measured` | The stored value is a direct reported measurement. |
| `fitted` | The stored value was fitted to observations or another dataset. |
| `literature` | The stored value was adopted from a publication without a new fit. |
| `calculated` | The stored value was calculated from other database records. |
| `assumed` | The stored value is an explicit assumption or default. |
| `assembled` | A composite selects and maps independently reviewed records. |

The evidence list separates publication roles from relationships to other
database records:

| Role | Points to | Meaning |
|---|---|---|
| `parameter_source` | source | States or derives the exact stored values. |
| `input_data` | source | Supplies observations or data used in the derivation. |
| `compilation` | source | Later republishes or collects the parameter values. |
| `validation` | source | Later evaluates the parameter or method. |
| `composition_source` | source | Explains or publishes the composite selection. |
| `input` | record | Supplies a database value used in an internal calculation. |
| `component` | record or archetype | Is selected into a reviewed composite slot. |
| `possible_duplicate` | record | May represent the same scientific assertion. |
| `related` | record | Helps adjudicate the finding without being a calculation input. |

Every source claim carries one or more structured locators: a table, figure,
equation, section, page, supplementary file, dataset or exact text location.
A source key existing in `db/sources.yml` is not enough; the locator records
where the claimed relationship was checked.

Every evidence item has an ID. Each finding cites the evidence IDs supporting
it, or records a scientific note when no supporting evidence can exist, as for
an unresolved question or explicit assumption. This allows the website to
explain what settled each claim and what remains open. For a definitive
`source`, `values`, or `method` finding on a measured, fitted or literature
record, the checker requires evidence IDs. The checker also verifies that IDs
are unique within an assessment, all cited IDs exist, and source and record
keys resolve.

For a measured, fitted or literature assessment to become sign-off eligible,
at least one `parameter_source` must equal the evidence record's `source` key.
If an assessment finds an exact-value publication for a record whose source is
different or `unreferenced`, its source finding is `correction_required`; that
record cannot be verified until a separate record-fix PR resolves the mismatch.
Internally `calculated` and explicitly `assumed` records are the deliberate
exception: they use `source: unreferenced`, do not invent a parameter
publication, and document the calculation or assumption in the sidecar.

An assessment reports a conclusion for each of eight review scopes: `name`,
`target`, `values`, `source`, `place`, `representativeness`, `method`, and
`identity`. The allowed conclusions are `supported`, `contradicted`,
`correction_required`, `unresolved`, `source_inaccessible`,
`curation_required`, and `not_applicable`. Findings may link related records
and tracking issues. This structure keeps duplicate-record and placement
problems reviewable instead of burying them in free text.

Calculated records also carry a `derivation`. `arithmetic_mean`,
`weighted_mean`, `scaled`, and `other` are internal calculations and require
`method: calculated` plus input-record evidence; means require at least two
inputs, while weighted means and scaling must record an expression.
`regression` is a fitted method and therefore requires
`method: fitted` plus a publication that states or derives the fitted values.
The checker detects missing records, self-reference and cycles.

### Assessment and verification states

An assessor stores only one of these assessment outcomes:

- `agent_assessed` — an automated assessment completed; its findings decide
  whether remediation or sign-off comes next;
- `unresolved` — available evidence does not settle the provenance;
- `source_inaccessible` — a necessary source could not be read;
- `curation_required` — the evidence leaves a choice for a maintainer.

`method` is required only for `agent_assessed`. An unresolved, inaccessible or
curation-required assessment may omit it when the available evidence does not
establish how the value was produced. The schema requires each non-agent
outcome to have at least one finding with the matching conclusion, and prevents
an `agent_assessed` result from hiding unresolved, inaccessible or curation
findings under that status.

`unaudited`, `awaiting_signoff`, and `verified` are derived record states, not
editable claims. The state is computed in this order:

1. no sidecar is `unaudited`;
2. `unresolved`, `source_inaccessible`, or `curation_required` is reported
   directly from the assessment outcome;
3. an `agent_assessed` sidecar with contradicted or correction-required
   findings remains `agent_assessed` until the record or assessment is fixed;
4. an assessment with only supported or not-applicable findings but without
   enough current eligible attestations is `awaiting_signoff`;
5. only the configured number of distinct eligible verifiers signing the
   current evidence and policy revisions produces `verified`.

`stale` describes an individual attestation whose evidence or policy revision
is no longer current. A record with only stale attestations is
`awaiting_signoff`, not a third editable record state.

An agent may create an assessment but may never create a verifier attestation
or set a verified state.

There are two distinct review layers. Atomic entries under `db/records/` use
an **evidence review**: values, parameter source, production method and source
locators are the central claims. Archetypes and typologies under
`db/archetypes/` use a **composition review**: component selection, composition
rationale, place applicability, completeness, compatible targets, and slot or
season mapping are reviewed. The composite page also shows each component's
independent evidence-review state. A composition sign-off does not re-verify
the underlying values. If a composite introduces a new scientific value, that
value must first be represented and evidence-reviewed as a record.

The two layers retain the same eight machine-stable finding keys so tooling
can derive one state model, but composition pages label their meaning
differently:

| Finding key | Evidence review | Composition review |
|---|---|---|
| `name` | record name | composite identity |
| `target` | parameter target | composite target |
| `values` | stored values | component selection |
| `source` | parameter source | composition rationale/source |
| `place` | observation place | place applicability |
| `representativeness` | value representativeness | composite representativeness |
| `method` | measurement/fit/calculation method | slot and season mapping |
| `identity` | duplicate/record identity | completeness and uniqueness |

A composition sidecar uses `method: assembled`, records each direct `uses:`
reference as `role: component`, and fingerprints those components as
dependencies. Changing a selected component, its content, or the mapping makes
the composition review stale without changing any record-level decision.

### GitHub-backed verifier attestations

Verifier eligibility, review scopes and thresholds are maintained in the
reviewed `.github/provenance-verifiers.yml` policy. Its revision is derived
from its complete canonical content rather than declared by an assessment.
An attestation records the verifier's GitHub handle and immutable numeric user
ID, decision, timestamp, a specific GitHub issue event, evidence
revision, verifier-policy revision and record scope. A handle written in YAML
or an issue body is not proof of identity: CI reloads the GitHub event and
verifies its actor ID, repository and URL against the reviewed verifier policy.
Events on older evidence or policy revisions are stale and do not contribute
to `verified`.

The authenticated event carries the decision payload: provenance-entry path,
review type, decision, scope, evidence revision, verifier-policy revision and any
superseded event. Every field must match the record assessment being reviewed.
An unrelated issue cannot therefore be reinterpreted as a sign-off.

The site sign-off button raises a prefilled GitHub issue. CI parses the issue
form, requires its author handle and immutable user ID to match the reviewed
verifier registry, and rejects stale record, evidence or policy revisions.
After successful validation, CI closes the issue as a completed review; a
rejected issue stays open and receives a comment stating the failure reason so
its author can correct or supersede it. When the author is not in the verifier
registry, that comment also links to the verifier-qualification request form.
Closed issues remain durable attestations and continue to be included in the
sweep.
Site builds sweep accepted sign-off issues and convert them to the same
attestation shape used for state derivation. The durable event remains the
linked GitHub issue. The audit agent must not receive or be able to invoke the
verifier's credential or sign-off action.

The offline checker validates sidecar shape, fingerprints, event anchors and
supersession graphs, but never authenticates GitHub identity itself. Without
authenticated event facts and the current central verifier policy, its state
derivation cannot return `verified`.

Stored attestations are immutable events; an edited issue-form decision is
revalidated and re-swept by CI. A later issue can supersede or withdraw an
earlier decision. Changing a parameter value, the source, place,
representativeness, method, evidence relationship, derivation or locator
changes the evidence revision, so earlier attestations no longer contribute to
the derived verified state.

The effective decision is computed, never copied into the sidecar as a status:

1. unless the assessment status is `agent_assessed`, all findings are
   `supported` or `not_applicable`, and the parameter-source alignment rule is
   satisfied, no attestation can produce `verified`;
2. attestations for a different evidence or verifier-policy revision are stale
   and ignored;
3. a withdrawal or superseding event removes the earlier event identified by
   both its event kind and numeric ID;
4. only attestations whose GitHub actor was eligible for the record's required
   scopes under the attestation's verifier-policy revision are considered;
5. any current `changes_requested`, `unresolved` or `curation_required`
   decision blocks verification until it is superseded;
6. otherwise, `verified` requires the configured number of distinct verifiers
   and coverage of every required scope.

Batch review is a user-interface convenience only. Each sidecar still receives
a record-scoped attestation, so the site can always answer who signed off each
database record and which evidence revision they reviewed.

### Revision fingerprints

Fingerprints use the prefix `sha256:` followed by the lowercase hexadecimal
SHA-256 digest of JSON canonicalized according to RFC 8785. The checker rejects
non-JSON types and non-finite numbers before hashing. Integer YAML mapping keys
used by hour-indexed profiles are projected to their exact decimal strings,
as required for JSON object keys; a string/integer key collision is rejected.

`record_revision` covers the complete parsed evidence record, including
display, attachment, seasonal and legacy fields. This deliberately favours a
safe false-positive re-review over allowing a scientific or identity change to
retain an old sign-off.

`dependency_revisions` contains the canonical fingerprints of every referenced
source-registry entry, place-registry entry and input or related evidence
record. A corrected DOI, a changed place definition or an updated input record
therefore invalidates the dependent review even when its short key is
unchanged.

`evidence_revision` covers `provenance_format_version`, `record_revision`,
`dependency_revisions`, and the assessment's `status`, `method`, `findings`,
`evidence`, `derivation`, `attempted_sources`, and `scientific_note` when
present. It excludes assessor identity, assessment time, `operational_note`,
its own digest field and all verification attestations. Re-running an unchanged
assessment therefore keeps the revision, while any reviewed scientific claim,
locator or dependency change invalidates earlier sign-offs.

### Illustrative shapes

The following fragments omit the common sidecar fields, assessment metadata,
and eight required findings. Angle-bracketed values are placeholders, not
database claims or proposed citations.

For a direct measurement:

```yaml
assessment:
  status: agent_assessed
  method: measured
  evidence:
    - id: parameter-publication
      source: <measurement-source-key>
      role: parameter_source
      locators:
        - {kind: table, label: <table-label>, page: <page>}
```

For a fitted parameter whose observations came from an earlier study:

```yaml
assessment:
  status: agent_assessed
  method: fitted
  evidence:
    - id: fitted-parameter-publication
      source: <parameter-source-key>
      role: parameter_source
      locators:
        - {kind: table, label: <table-label>, page: <page>}
    - id: observation-input
      source: <observation-source-key>
      role: input_data
      locators:
        - {kind: dataset, label: <dataset-or-study-site>}
```

For a literature value that a later paper compiled without becoming its
parameter source:

```yaml
assessment:
  status: agent_assessed
  method: literature
  evidence:
    - id: original-parameter-publication
      source: <parameter-source-key>
      role: parameter_source
      locators:
        - {kind: equation, label: <equation-label>, page: <page>}
    - id: later-compilation
      source: <later-source-key>
      role: compilation
      locators:
        - {kind: table, label: <table-label>, page: <page>}
```

For an internal arithmetic mean:

```yaml
assessment:
  status: agent_assessed
  method: calculated
  evidence:
    - {id: first-input, record: records/<first-input>, role: input}
    - {id: second-input, record: records/<second-input>, role: input}
  derivation:
    kind: arithmetic_mean
    expression: (<first-input> + <second-input>) / 2
```

For an explicit assumption with no invented publication:

```yaml
assessment:
  status: agent_assessed
  method: assumed
  evidence: []
  scientific_note: <why the assumption exists and its intended scope>
```

For an unresolved assessment there is no invented source:

```yaml
assessment:
  status: unresolved
  evidence: []
  scientific_note: <what was checked and what would settle the question>
```

For an inaccessible source, the failed retrieval is recorded without inventing
a citation:

```yaml
assessment:
  status: source_inaccessible
  evidence: []
  attempted_sources:
    - description: <legacy-reference-or-source-description>
      url: <attempted-url>
      attempted_at: <timestamp>
      outcome: inaccessible
      note: <what was attempted and what access would settle it>
  scientific_note: <which findings remain unverified>
```

Human verification is an authenticated event bound to the evidence revision:

```yaml
verification:
  attestations:
    - verifier: <verified-github-handle>
      verifier_id: <immutable-github-user-id>
      decision: verified
      signed_at: <github-event-timestamp>
      event:
        kind: issue
        id: <github-issue-number>
        url: "https://github.com/UMEP-dev/SUEWS-database/issues/<number>"
      evidence_revision: sha256:<64-lowercase-hex-digits>
      verifier_policy_revision: sha256:<64-lowercase-hex-digits>
      scope: record
```

Profile records (`records/profiles/<kind>/`) hold 24-hour profiles keyed
1..24 with `working_day` and `holiday` sides, matching supy's
`HourlyProfile`; `attaches_to` names the exact config path the profile
plugs into (e.g. `anthropogenic_emissions.co2.traffprof_24hr`). The LUCY
per-country profiles live one level deeper (`traffic/lucy/<country>.yml`).

Materials and constructions describe building fabric: a material record
carries the five thermal/radiative properties of one material; a
construction record layers materials with thicknesses for a roof and wall,
ready to be compiled into supy's `vertical_layers`.

## An archetype

```yaml
archetype: archetypes/surfaces/bldgs/helsinki--kumpula
schema_version: '2026.5'
target: land_cover.bldgs
name: Kumpula
place: helsinki
uses:
  albedo: records/surfaces/bldgs/london--ward2016--albedo
  emissivity: records/surfaces/bldgs/helsinki--jarvi2014--emissivity
  drainage: records/surfaces/bldgs/helsinki--jarvi2011--drainage
  ohm:
    summer_wet: records/ohm/helsinki--jarvi2014--buildings
    summer_dry: records/ohm/helsinki--jarvi2014--buildings
    winter_wet: records/ohm/helsinki--jarvi2014--buildings
    winter_dry: records/ohm/helsinki--jarvi2014--buildings
```

`uses:` slots name the role each referenced record plays. OHM is the one
structured slot: the four season/wetness positions of supy's `ohm_coef` are
an assembly decision, so they live here rather than on the coefficient
records. An archetype may also carry its own `parameters:` (scalars asserted
by the legacy composite row itself, e.g. the snow entries) — same rules as a
record.

Because an archetype references records, both provenance layers are
inspectable. The Kumpula page shows that its albedo comes from a London study
and the evidence-review state of that record. A separate composition sidecar
can document and sign off why that London value was selected for Kumpula and
how every component was mapped; it does not replace the record-level review.

### Typology photographs

A typology is a visual idea, so the site shows a photograph of one where it
can. `db/images.yml` decides which: a typology gets a photograph only if it
is listed there, with the credit and licence the photograph may not be shown
without.

```yaml
release: typology-images-20260821

images:
  archetypes/typologies/sweden--modernism:
    file: sweden--modernism.jpg
    origin_url: https://upload.wikimedia.org/.../1280px-Rinkeby_mot_nordost_1988.jpg
    description_page: https://commons.wikimedia.org/wiki/File:Rinkeby_mot_nordost_1988.jpg
    credit: Holger Ellgaard
    licence: CC BY-SA 3.0
    licence_url: https://creativecommons.org/licenses/by-sa/3.0/
    caption: Rinkeby, Stockholm, seen from the north-east in May 1988
    sha256: 12a43617...
    bytes: 279960
    width: 1280
    height: 849
```

The image files are not repository content. They are individual assets on
the release named at the top of the manifest, and `scripts/build_site.py`
fetches each one from there, checks it against its `sha256`, and publishes
it under the site's own origin. A reader's browser therefore contacts nobody
but the site, and an upstream link that rots cannot blank a page.
`scripts/fetch_images.py` rebuilds that asset set from the recorded origins;
it is the only thing here that contacts them.

The typology records themselves are untouched: their legacy `url` and
`image_source` strings stay verbatim as migrated, and the site reads
neither. Where a licence could not be established from the source, the
record goes under `unresolved:` with a `reason` and a
`what_would_settle_it`, and no image is shown. `make check` requires every
typology carrying a `url` to appear in one section or the other, so an
omission is deliberate rather than silent, and requires a credit and a
licence on everything in `images:`, because that is what publishing under
these licences demands. An `unresolved:` entry may also carry `tracked_by`,
the number of the issue where the question is being settled; the site links
a reader to it rather than leaving the absence unexplained.

A typology with no published photograph carries an invitation to offer one,
backed by the `typology-photo.yml` issue form. The form makes the
photographer and the licence required fields, and the page says so before
anyone starts looking: a photograph offered without terms cannot be
published, and finding that out afterwards wastes the contributor's effort
rather than ours.

#### Offering a photograph

Two things are needed and nothing else: somewhere the photograph can be
fetched from, and terms under which it may be republished with credit.
Public domain, CC0 and the Creative Commons licences all qualify. For
someone else's photograph, the link to the page stating those terms is the
whole of it — a Wikimedia Commons file page or a Flickr photo page usually
says so plainly. For your own, the issue form's declaration is the grant,
made from your own account and timestamped, which is why it asks you to
choose the licence deliberately: a licence cannot be withdrawn from copies
already published.

The credit you give is published as you write it, so it may be a name, a
pseudonym or a username — Creative Commons licences allow attribution by
pseudonym, and the site does not care which you use. It persists: a credit
travels into this repository, its history and its release assets, and stays
there after the record itself changes.

Do not post an email address or any other private contact detail in the
issue. None is needed — a GitHub account is contact enough, and questions
are asked in the thread — and a public issue keeps what is posted to it,
including in its edit history.

Photographs should show a street or a group of buildings rather than any
individual. Anyone visible should be incidental to the view.

## Using the database with SUEWS

```sh
python scripts/export_record.py archetypes/surfaces/bldgs/helsinki--kumpula
```

renders a model-ready fragment: every value becomes supy's RefValue form

```yaml
alb:
  value: 0.12
  ref: {ID: ward2016, DOI: 10.1016/j.uclim.2016.05.001, desc: london, city}
```

which pastes directly into a SUEWS YAML configuration under the path named
by `target` (`sites[].properties.land_cover.bldgs` here). The citation
travels with the value into the user's config. This is verified end to end:
`make validate` checks every fragment against the supy data model
(`PavedProperties`, `SnowParams`, `HourlyProfile`, ...), and fragments
merged into supy's sample configuration load through
`SUEWSConfig.from_yaml`.

## Adding a value

1. Copy the nearest record in `db/records/` as a template. Name the file
   `<place>--<sourcekey>--<what>.yml` and set `record:` to match its path.
2. Use supy's canonical parameter names (the existing records and
   `schema/table_mapping.yml` show the vocabulary; supy's own docs are the
   reference).
3. Add the citation to `db/sources.yml` (key: `<firstauthor><year>`) and,
   if the place is new, add it to `db/places.yml`.
4. Set `place`, `representativeness` and `method` honestly. `generic` is a
   valid answer; a fabricated site is not.
5. Run `make check` (structure, references, coupling rules) and, with supy
   installed, `make validate`. Open a PR; CI runs the same checks.

One value per PR is fine. A full surface description is better assembled as
an archetype referencing your new records.

## Versioning and migration

The whole database sits in git: a value's history is `git log` on its
record file, and a contribution is a reviewable diff of one or two small
YAML files. `schema_version` pins each record to the supy schema its names
were verified against; when supy's data model changes shape, records are
migrated mechanically (supy ships schema-migration tooling) and the pin
advances in the same commit — the same discipline the migration itself
followed, where `schema/migration_census.yml` proves every legacy cell
landed exactly once.
