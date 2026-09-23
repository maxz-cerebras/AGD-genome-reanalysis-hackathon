"""HTTP API + static UI.  Run: ``uvicorn kinquery.server:app --reload``."""

from __future__ import annotations

import os
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from .hpo import HPO
from .matcher import Matcher, describe
from .nlq import MODELS, Translator
from .query import Query
from .registry import Registry

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("KINQUERY_DATA", ROOT.parents[2] / "session-2" / "data" / "phenopackets.jsonl"))
STATIC = ROOT / "static"

EXAMPLES = [
    "female, born 1965–2005, with a first- or second-degree relative with breast cancer",
    "confirmed finding in BRCA1 or BRCA2",
    "clinical or molecular diagnosis of Lynch syndrome",
    "child under 10 with seizures and no molecular diagnosis",
    "two or more relatives with a recorded phenotype or diagnosis",
    "male with intellectual disability whose mother or maternal uncle also has intellectual disability",
    "patient with a colon cancer phenotype but no molecular or clinical diagnosis",
]


class State:
    def __init__(self):
        self.registry = Registry.load(DATA)
        self.hpo = HPO.load()
        self.matcher = Matcher(self.registry, self.hpo)
        self._translator: Translator | None = None

    @property
    def translator(self) -> Translator:
        if self._translator is None:
            self._translator = Translator(self.registry, self.hpo)
        return self._translator


@lru_cache(maxsize=1)
def state() -> State:
    return State()


app = FastAPI(title="kinquery", version="0.1.0")


class TranslateRequest(BaseModel):
    text: str
    model: Literal["haiku", "sonnet"] | None = None


class SearchRequest(BaseModel):
    query: dict


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/browse")
def browse():
    return FileResponse(STATIC / "browse.html")


@app.get("/api/models")
def models():
    return MODELS


@app.get("/api/patients")
def patients():
    """One row per patient record, for the registry browser."""
    rows = []
    for fam in state().registry.families:
        p = fam.proband
        rows.append({
            "patient_id": p.id, "family_id": fam.id, "sex": p.sex, "birth_year": p.birth_year,
            "features": [{"id": f.id, "label": f.label, "negated": f.negated} for f in p.features],
            "genes": [g.symbol for g in p.genes],
            "diseases": [{"id": d.id, "label": d.label, "evidence": d.evidence} for d in p.diseases],
            "relatives": len(fam.members) - 1,
            "affected_relatives": sum(1 for m in fam.members.values()
                                      if not m.is_proband and (any(not f.negated for f in m.features) or m.diseases or m.genes)),
        })
    return rows


@app.get("/api/family/{patient_id}/raw")
def family_raw(patient_id: str):
    fam = state().registry.by_proband.get(patient_id)
    if fam is None:
        raise HTTPException(404, f"unknown patient {patient_id}")
    return fam.raw


@app.get("/api/examples")
def examples():
    return EXAMPLES


@app.get("/api/vocab")
def vocab():
    return state().registry.vocabulary


@app.get("/api/schema")
def schema():
    return Query.model_json_schema()


@app.post("/api/translate")
def translate(req: TranslateRequest):
    if not req.text.strip():
        raise HTTPException(400, "empty criteria text")
    try:
        t = state().translator.translate(req.text, model=req.model)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {
        "query": t.query.model_dump(exclude_none=True),
        "interpretation": t.interpretation,
        "assumptions": t.assumptions,
        "notes": t.notes,
        "warnings": t.warnings,
        "model": t.model,
    }


@app.post("/api/search")
def search(req: SearchRequest):
    try:
        query = Query.model_validate(req.query)
    except ValidationError as e:
        raise HTTPException(422, e.errors()) from e
    return state().matcher.search(query)


@app.get("/api/family/{patient_id}")
def family(patient_id: str):
    fam = state().registry.by_proband.get(patient_id)
    if fam is None:
        raise HTTPException(404, f"unknown patient {patient_id}")
    gens = fam.generations()
    members = []
    for m in fam.members.values():
        rel = fam.relations.get(m.id)
        members.append({
            "id": m.id, "path": m.path, "sex": m.sex, "birth_year": m.birth_year,
            "father": m.father_id, "mother": m.mother_id, "generation": gens[m.id],
            "is_proband": m.is_proband,
            "relation": describe(rel) if rel else "self",
            "degree": rel.degree if rel else 0,
            "features": [{"id": f.id, "label": f.label, "negated": f.negated, "path": f.path} for f in m.features],
            "genes": [{"id": g.id, "symbol": g.symbol, "path": g.path} for g in m.genes],
            "diseases": [{"id": d.id, "label": d.label, "evidence": d.evidence, "path": d.path} for d in m.diseases],
        })
    return {"family_id": fam.id, "proband_id": fam.proband.id, "members": members}


@app.get("/api/hpo/search")
def hpo_search(q: str):
    return [{"id": i, "label": l} for i, l in state().hpo.search(q, limit=15)]


app.mount("/static", StaticFiles(directory=STATIC), name="static")
if (ROOT / "slides").is_dir():
    app.mount("/slides", StaticFiles(directory=ROOT / "slides"), name="slides")


@app.get("/api/hpo/term/{hid}")
def hpo_term(hid: str):
    """What a phenotype criterion actually matches: descendant count and the registry terms in scope."""
    st = state()
    if hid not in st.hpo:
        raise HTTPException(404, f"{hid} not in HPO 2024-08-13")
    scope = st.hpo.descendants(hid)
    in_registry = {k: v for k, v in st.registry.vocabulary["hpo"].items() if k in scope}
    return {"id": hid, "label": st.hpo.label[hid], "descendants": len(scope) - 1,
            "registry_terms": [{"id": k, "label": v} for k, v in sorted(in_registry.items(), key=lambda kv: kv[1])]}
