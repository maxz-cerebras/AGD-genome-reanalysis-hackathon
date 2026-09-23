# kinquery — Challenge 8: find patients matching trial criteria

A study coordinator types inclusion criteria in plain English. Claude translates
them into a **structured query** that is shown back as criteria cards for
confirmation (and can be edited as JSON). The confirmed query is evaluated
deterministically against the 100 family phenopackets; every match lists the
exact fields that satisfied each criterion, and the pedigree is drawn with the
qualifying relatives highlighted. Patients whose record lacks the data a
criterion needs are reported separately as *could not be assessed*.

## Run

```sh
cd session-2/projects/kinquery
uv sync
export ANTHROPIC_API_KEY=...            # only needed for translation
uv run uvicorn kinquery.server:app --port 8765
open http://127.0.0.1:8765            # cohort finder
open http://127.0.0.1:8765/browse     # registry browser: all 100 records, pedigrees, raw JSON
```

First start downloads `hp.json` (HPO 2024-08-13, 21 MB) into `.cache/`.
The UI offers Haiku or Sonnet for translation (`claude-haiku-4-5-20251001`,
`claude-sonnet-4-6`; override with `KINQUERY_MODEL_HAIKU` / `KINQUERY_MODEL_SONNET`,
default choice with `KINQUERY_MODEL=haiku|sonnet`). `KINQUERY_DATA` overrides the phenopackets path.
Never commit an API key; pass it through the environment only.

Tests: `uv run pytest` (two tests call the live model and are skipped without a key).

## Query categories

| kind | meaning | source fields |
|---|---|---|
| `sex` | FEMALE / MALE | `subject.sex` |
| `birth_year` | inclusive range | `subject.dateOfBirth` (year precision) |
| `age` | `min` inclusive, `max` exclusive, at `reference_date` | `subject.dateOfBirth` |
| `phenotype` | HPO term, `present` / `excluded` (negated) / `absent`; descendants via ontology | `phenotypicFeatures[]` |
| `gene` | any of the symbols — a finding, not a diagnosis | `genes[]` |
| `diagnosis` | disease terms (or any), `evidence` = molecular `MIM:` and/or clinical `ORDO:` | `diseases[]` |
| `has_record` | any recorded phenotype / diagnosis / gene | all of the above |
| `relative` | family history: degree range, roles (mother, sibling, aunt…), lineage, sex, `min_count`, nested `where` | `pedigree.persons` + relatives' records |
| `all` / `any` / `not` | boolean composition; `any` takes `min_count` for “at least N of the following” | |

A query has `criteria` (inclusion, all must hold) and `exclusions` (none may
hold). An exclusion that cannot be evaluated (e.g. age with no DOB) makes the
patient *could not be assessed* rather than eligible. Results include a
per-criterion funnel (how many patients satisfy each criterion independently)
and a CSV export of the matched cohort with evidence.

Not answerable from this export, whatever the query: variant pathogenicity or
zygosity, age at onset, treatments, lab values, vital status, ancestry.
“Pathogenic variant in X” is mapped to a `gene` finding and the simplification
is shown as an assumption.

## Design notes

* **Family relationships are computed, not stored.** `registry.Family.relation`
  derives the coefficient of relationship from `paternalId`/`maternalId` links
  (degree = −log₂ r: parent/sibling/child = 1, grandparent/aunt/half-sibling = 2,
  first cousin = 3), a human label (“half-brother”, “maternal aunt”, “first
  cousin once removed”), lineage and generation. Partners and in-laws are
  `degree None` and never satisfy a `relative` criterion.
* **Three-valued matching.** Each criterion is `met`, `unmet` or `unknown`
  (missing data), combined with Kleene logic, so a missing `dateOfBirth`
  produces *could not be assessed* rather than silent exclusion.
* **Grounding.** The model sees the registry's disease and gene vocabulary and
  the query JSON schema; its HPO ids are checked against the ontology and any
  correction or unresolved term is surfaced in the confirmation step.
* Three different things can look like “has the disease”: a `gene` finding, a
  `MIM:` molecular diagnosis and an `ORDO:` clinical diagnosis. They are
  separate criteria so a trial can accept some and not others.

## Answers to the judging probes

* **Pedigree traversal** — every family-history evidence line prints the
  parent-link chain, e.g. `P0003001 →maternalId→ 0 →maternalId→ 3` for a
  maternal grandmother; the pedigree drawing highlights the same person.
* **Descendants or label string?** — HPO subsumption. The phenotype block shows
  how many descendants are in scope and which of them occur in the registry;
  evidence lines name the matched descendant and the query term it falls under.
* **No date of birth** — the patient is listed under *could not be assessed*
  with the exact missing field (`proband.subject.dateOfBirth`), never dropped
  or assumed.

## What Claude got wrong, and how it was caught

* Hallucinated HPO ids with correct labels (`HP:0010582` for “Colonic polyposis”
  is actually *Irregular epiphyses*; Haiku's `HP:0004395` for “Colonic polyps”
  is *Malnutrition*). Grounding now re-resolves by label and warns when no exact
  term exists, listing candidates.
* “At least two of …” silently approximated as OR until the schema could
  express `min_count`; the model itself flagged the approximation in its
  assumptions, which is how it was noticed.
* Tool-use payload occasionally wrapped under an arbitrary key; validated with
  pydantic and unwrapped rather than trusted.
