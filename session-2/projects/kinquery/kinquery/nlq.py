"""Natural language -> structured Query via Claude tool use, then grounding.

The model is given the registry's disease and gene vocabulary and the query
schema as a tool, and must call the tool. Its output is then *grounded*:
HPO ids are checked against the ontology (label mismatches corrected, unknown
ids re-resolved by label), disease ids against the registry vocabulary, gene
symbols normalised. Every correction is reported so the user can see it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date

import anthropic
from pydantic import ValidationError

from .hpo import HPO
from .query import (
    DiagnosisCriterion, GeneCriterion, GroupCriterion, NotCriterion, PhenotypeCriterion, Query,
    RelativeCriterion,
)
from .registry import Registry

MODELS = {
    "haiku": os.environ.get("KINQUERY_MODEL_HAIKU", "claude-haiku-4-5-20251001"),
    "sonnet": os.environ.get("KINQUERY_MODEL_SONNET", "claude-sonnet-4-6"),
}
MODEL = MODELS[os.environ.get("KINQUERY_MODEL", "sonnet")]

SYSTEM = """You translate trial inclusion criteria written in plain English into a structured cohort query
over a registry of family phenopackets (PhenoTips export). Call the `submit_query` tool exactly once.

Data model facts you must respect:
- Each patient (proband) has sex, dateOfBirth (year precision, sometimes missing), HPO phenotypicFeatures
  (a feature may be `negated`, meaning looked for and found absent), `genes` (a gene finding, NOT a diagnosis),
  and `diseases`: `MIM:` ids are molecular (final) diagnoses, `ORDO:` ids are clinical diagnoses.
- Family history is NOT on the proband. Use a `relative` criterion; degree of relationship is computed from
  the pedigree: 1st degree = parents, full siblings, children; 2nd = grandparents, aunts/uncles,
  half-siblings, nieces/nephews; 3rd = first cousins. "first- or second-degree relative" -> degree_min 1, degree_max 2.
  "relative" without qualifier -> degree_min 1, degree_max null. "mother has X" -> roles ["mother"].
- Phenotypes: give the canonical HPO id and label (e.g. HP:0001250 "Seizure"). Descendant terms match
  automatically (include_descendants true). "no seizures" -> status "absent". "seizures ruled out" -> "excluded".
- "confirmed finding in GENE" / "carries GENE" / "GENE mutation" -> `gene` criterion.
- "molecular diagnosis" -> diagnosis with evidence ["molecular"]; "clinical diagnosis" -> ["clinical"];
  "diagnosed with X" / "clinical or molecular" -> both. "no molecular diagnosis" -> not(diagnosis terms null, evidence molecular).
- For diseases, pick ids ONLY from the registry disease vocabulary below; include every id that denotes the
  disease concept (e.g. all Lynch syndrome / HNPCC entries). Cancers named as phenotypes ("breast cancer" in a
  relative) are HPO phenotypes (HP:0003002 Breast carcinoma), unless the text says diagnosis/syndrome.
- Age: "under 10" -> age max 10 (exclusive); "over 18" -> min 19; "18 or older" -> min 18. Year ranges -> birth_year.
  "child" alone -> age max 18.
- "two or more relatives with a recorded phenotype or diagnosis" -> relative min_count 2, where has_record of [phenotype, diagnosis].
- Protocols list inclusion and exclusion criteria: put inclusions in `criteria` and exclusions, stated POSITIVELY,
  in `exclusions` (e.g. "Exclude Li-Fraumeni" -> exclusions: [diagnosis Li-Fraumeni]; "must not have a molecular
  diagnosis" -> exclusions: [diagnosis evidence molecular]). Use `not` only inside nested logic.
- "at least two of: A, B, C" -> {"kind":"any","min_count":2,"criteria":[A,B,C]}. Plain "A or B" -> any without min_count.
- Variant pathogenicity, age at onset, treatments and lab values are NOT in the data; map "pathogenic variant in X"
  to a `gene` criterion and record the simplification in `assumptions`.
- Do not invent criteria that are not stated. Put anything ambiguous into `assumptions`.
"""


@dataclass
class Translation:
    query: Query
    interpretation: str
    assumptions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # grounding corrections
    warnings: list[str] = field(default_factory=list)  # unresolved items
    model: str = MODEL
    raw: dict | None = None


def _tool_schema() -> dict:
    schema = Query.model_json_schema()
    schema.pop("title", None)
    props = schema["properties"]
    props["interpretation"] = {"type": "string", "description": "One-sentence restatement of the criteria as you understood them"}
    props["assumptions"] = {"type": "array", "items": {"type": "string"},
                            "description": "Interpretive choices the coordinator should confirm"}
    schema["required"] = ["criteria", "interpretation", "assumptions"]
    return schema


class Translator:
    def __init__(self, registry: Registry, hpo: HPO, client: anthropic.Anthropic | None = None, model: str = MODEL):
        self.registry = registry
        self.hpo = hpo
        self.client = client or anthropic.Anthropic()
        self.model = model
        vocab = registry.vocabulary
        diseases = "\n".join(f"  {k}\t{v}" for k, v in sorted(vocab["diseases"].items()))
        genes = ", ".join(sorted(vocab["genes"]))
        self.system = SYSTEM + f"\nRegistry disease vocabulary (id<TAB>label):\n{diseases}\n\nRegistry gene symbols: {genes}\n"

    def translate(self, text: str, reference_date: date | None = None, model: str | None = None) -> Translation:
        ref = (reference_date or date.today()).isoformat()
        model = MODELS.get(model, model) if model else self.model
        resp = self.client.messages.create(
            model=model, max_tokens=4000, system=self.system,
            tools=[{"name": "submit_query",
                    "description": "Submit the structured cohort query. Top-level fields: criteria (list), interpretation, assumptions.",
                    "input_schema": _tool_schema()}],
            tool_choice={"type": "tool", "name": "submit_query"},
            messages=[{"role": "user", "content": f"Today is {ref}. Criteria:\n\n{text}"}],
        )
        block = next(b for b in resp.content if b.type == "tool_use")
        raw = dict(block.input)
        if "criteria" not in raw:  # model sometimes wraps the payload under an arbitrary key
            wrapped = [v for v in raw.values() if isinstance(v, dict) and "criteria" in v]
            if len(wrapped) == 1:
                raw = {**wrapped[0], **{k: v for k, v in raw.items() if not isinstance(v, dict)}}
        interpretation = raw.pop("interpretation", "")
        assumptions = list(raw.pop("assumptions", []) or [])
        raw["reference_date"] = ref
        try:
            query = Query.model_validate(raw)
        except ValidationError as e:  # surface the model's structural mistake instead of crashing
            raise ValueError(f"model produced an invalid query: {e.errors()[0]['msg']} at {e.errors()[0]['loc']}") from e
        t = Translation(query, interpretation, assumptions, model=model, raw=raw)
        self.ground(t)
        return t

    # -- grounding ----------------------------------------------------------

    def ground(self, t: Translation) -> None:
        for c in [*t.query.criteria, *t.query.exclusions]:
            self._ground_criterion(c, t)

    def _ground_criterion(self, c, t: Translation) -> None:
        if isinstance(c, PhenotypeCriterion):
            term = c.term
            by_label = self.hpo.lookup(term.label) if term.label else None
            known = term.id in self.hpo
            if known and (by_label is None or by_label == term.id):
                canon = self.hpo.label[term.id]
                if by_label is None and term.label and term.label.lower() != canon.lower():
                    cands = [(i, l) for i, l in self.hpo.search(term.label, limit=4) if i != term.id]
                    hint = "; did you mean " + ", ".join(f"{i} {l}" for i, l in cands) + "?" if cands else ""
                    t.warnings.append(f"id/label disagree: {term.id} is '{canon}' in HPO, model wrote '{term.label}'. "
                                      f"Query uses {term.id} as given{hint}")
                term.label = canon
            elif by_label:  # the label names the intended concept; the id was wrong or hallucinated
                what = f"{term.id} ('{self.hpo.label[term.id]}')" if known else f"unknown id {term.id}"
                t.notes.append(f"'{term.label}': {what} replaced by {by_label} ({self.hpo.label[by_label]})")
                term.id, term.label = by_label, self.hpo.label[by_label]
            else:
                cands = self.hpo.search(term.label or term.id, limit=3)
                hint = "; candidates: " + ", ".join(f"{i} {l}" for i, l in cands) if cands else ""
                t.warnings.append(f"HPO term {term.id} '{term.label}' not found in HPO 2024-08-13{hint}")
        elif isinstance(c, DiagnosisCriterion) and c.terms:
            vocab = self.registry.vocabulary["diseases"]
            for term in c.terms:
                if term.id in vocab:
                    term.label = vocab[term.id]
                else:
                    hits = [k for k, v in vocab.items() if term.label and term.label.lower() in v.lower()]
                    if len(hits) == 1:
                        t.notes.append(f"disease {term.id} '{term.label}' not in registry; mapped to {hits[0]} by label")
                        term.id, term.label = hits[0], vocab[hits[0]]
                    else:
                        t.warnings.append(f"disease {term.id} '{term.label}' is not present in the registry vocabulary; it will match nobody")
        elif isinstance(c, GeneCriterion):
            known = {g.upper() for g in self.registry.vocabulary["genes"]}
            c.symbols = [s.upper() for s in c.symbols]
            for s in c.symbols:
                if s not in known:
                    t.warnings.append(f"gene {s} has no finding in the registry; it will match nobody")
        elif isinstance(c, NotCriterion):
            self._ground_criterion(c.criterion, t)
        elif isinstance(c, GroupCriterion):
            for k in c.criteria:
                self._ground_criterion(k, t)
        elif isinstance(c, RelativeCriterion) and c.where is not None:
            self._ground_criterion(c.where, t)
