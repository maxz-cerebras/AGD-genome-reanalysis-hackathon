# kinquery — slide content for the team template

Paste into your copy of the Google Slides template (one section per slide).
Speaker notes follow each slide. Numbers are from the live tool on
`session-2/data/phenopackets.jsonl`; every figure has a screen it comes from.
Screenshots for slide 4 are in this folder.

---

## Slide 1 — Team, challenge, members  (15 s)

**kinquery** — Challenge 8: find patients matching trial criteria
Team: Jakob & Max

> Notes: "We took challenge 8. Study coordinator, protocol in hand, 100 family
> records in PhenoTips. Which patients qualify, and why."

---

## Slide 2 — The problem  (30 s)

**Who:** a study coordinator. **When:** the afternoon a protocol arrives and
the PI asks "how many of ours would qualify?"

- Criteria arrive as prose: *"female, born 1965–2005, first- or second-degree
  relative with breast cancer; exclude Li-Fraumeni"*.
- The answer is not in the patient record. Family history lives in relatives
  and the pedigree; **degree of relationship is not stored anywhere**.
- Three things look like "has the disease": a gene finding, a MIM molecular
  diagnosis, an ORDO clinical diagnosis. Trials accept some, not others.
- "Seizures" must match *Generalized myoclonic seizure* — an ontology
  question, not a string search.
- 20 of 100 probands have no date of birth. An age criterion has to say what
  it did with them.

> Notes: name the user and the moment. Manually this is reading 100 pedigrees.
> The point is not "LLM" — it is that every answer must be traceable.

---

## Slide 3 — How we addressed it  (1 min)

**Plain text → structured query (shown, editable) → deterministic matching → evidence**

1. Claude (Haiku or Sonnet, selectable) translates the text into a **typed
   query schema** — sex, birth year / age, HPO phenotype, gene finding,
   diagnosis (molecular / clinical), family history, at-least-N-of, NOT;
   inclusion and exclusion zones.
2. The query is **grounded**: HPO ids checked against HPO 2024-08-13, disease
   ids against the registry vocabulary. Corrections and unresolved terms are
   shown to the coordinator, who confirms or edits blocks graphically.
3. **The model never touches the data.** Matching is code: pedigree parent
   links → computed relationship degree; HPO subsumption; three-valued logic
   (met / unmet / *could not be assessed*).
4. Output: every match with the exact fields that qualified it, the pedigree
   with the qualifying relatives highlighted, a per-criterion funnel, CSV.

> Notes: the LLM writes the question, not the answer. Everything on the
> results screen is computed from the file and points at a JSON path.

---

## Slide 4 — The result  (2 min) — SCREENSHOTS

Use `2-structured-query.png`, `3-results-evidence.png`, `4-could-not-be-assessed.png`.

Live demo script (same query as the screenshots):

> *female, born 1965–2005, with a first- or second-degree relative with breast
> cancer; exclude patients with a molecular diagnosis of Li-Fraumeni syndrome*

- Structured query appears as INCLUDE / EXCLUDE blocks; the model's
  assumptions are listed ("breast cancer in a relative = HPO Breast carcinoma,
  not a diagnosis").
- Run → **10 matched · 3 could not be assessed · 87 not matched**.
  Funnel: 56 female · 35 born 1965–2005 · 16 with a qualifying relative ·
  1 excluded by Li-Fraumeni.
- Open P0003001: *mother (1st degree, maternal) →
  relatives[0].phenotypicFeatures[0] = HP:0003002 Breast carcinoma*, traversal
  `P0003001 →maternalId→ 0`; grandmother via `→maternalId→ 0 →maternalId→ 3`.
  Pedigree highlights both.
- Tab "could not be assessed": P0003005 — sex met, family history met,
  **birth year: no dateOfBirth recorded**. Not dropped, not assumed.
- Probe answers ready: seizures query shows "matches the term and its 346 HPO
  descendants; present in this registry: Generalized myoclonic seizure,
  Infantile spasms, …". Child <10 + seizures + no molecular dx → 3 matched,
  1 unassessable (P0003088, no DOB).

> Notes: if the live demo breaks, the screenshots are the same run. Say so.

---

## Slide 5 — Limits, and working with Claude  (45 s)

**Limits (honest):**
- Cohort = 100 probands; relatives are pedigree entries, not patient records.
- Age from birth *year* only; most relatives have no DOB.
- No variant pathogenicity, onset age, treatments in the export — "pathogenic
  BRCA1 variant" becomes "BRCA1 finding", flagged as an assumption.
- Synthetic data; 100 families in memory. At registry scale the matcher needs
  an index, the pedigree math does not change.

**What Claude got wrong — and how we caught it:**
- Hallucinated HPO ids with correct labels: `HP:0010582 "Colonic polyposis"` is
  actually *Irregular epiphyses*; Haiku's `HP:0004395 "Colonic polyps"` is
  *Malnutrition*. Caught by ontology grounding — the label now wins, and
  unresolvable terms are shown with candidates instead of silently used.
- "At least two of A, B, C" was approximated as OR (12 matches instead of 3)
  until the schema could express it — the model itself flagged the shortcut in
  its assumptions list.
- Tool output sometimes wrapped under a random key; validated with a schema,
  never trusted.
- **Solved the wrong problem first.** Claude latched onto the pedigree
  (relationship maths, drawing, labels) and built a family-relationship tool.
  We redirected it: the user is a coordinator constructing a *cohort*; the
  pedigree is evidence, not the product. Inclusion/exclusion, "at least N of"
  and the funnel only came after that correction.
- Claude Code built the pedigree-degree maths and the ontology loader; we
  verified degrees on a hand-drawn family (half-aunt = 3rd degree, first cousin
  = 3rd, partner = unrelated) before trusting any count.

> Notes: judges score this slide directly. Be concrete: two wrong HPO ids,
> one wrong logic shortcut, how each surfaced.
