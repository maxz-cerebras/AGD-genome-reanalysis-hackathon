"""End-to-end: text -> /api/translate (live Claude) -> /api/search -> evidence -> /api/family."""
import os

import pytest
from fastapi.testclient import TestClient

from kinquery.server import app

client = TestClient(app)
live = pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")


def test_search_and_family_without_llm():
    q = {"criteria": [{"kind": "relative", "degree_max": 2, "where": {"kind": "phenotype", "term": {"id": "HP:0003002"}}}]}
    r = client.post("/api/search", json={"query": q})
    assert r.status_code == 200
    body = r.json()
    p = next(p for p in body["matched"] if p["patient_id"] == "P0003002")
    field = p["outcomes"][0]["children"][0]["evidence"][1]["field"]
    assert field == "relatives[4].phenotypicFeatures[0]"
    fam = client.get("/api/family/P0003002").json()
    aunt = next(m for m in fam["members"] if m["id"] == "4")
    assert aunt["relation"] == "aunt (2nd degree, paternal)" and aunt["generation"] == -1
    assert aunt["features"][0]["path"] == field  # UI can highlight exactly the qualifying field


def test_search_rejects_invalid_query():
    r = client.post("/api/search", json={"query": {"criteria": [{"kind": "bogus"}]}})
    assert r.status_code == 422


@live
def test_translate_then_search_child_seizures():
    r = client.post("/api/translate", json={"text": "child under 10 with seizures and no molecular diagnosis"})
    assert r.status_code == 200, r.text
    t = r.json()
    q = t["query"]
    # "no molecular diagnosis" may come back as an exclusion or as NOT(...)
    kinds = sorted(c["kind"] for c in q["criteria"]) + ["excl:" + c["kind"] for c in q.get("exclusions", [])]
    assert kinds in (["age", "not", "phenotype"], ["age", "phenotype", "excl:diagnosis"]), q
    pheno = next(c for c in q["criteria"] if c["kind"] == "phenotype")
    assert pheno["term"] == {"id": "HP:0001250", "label": "Seizure"}
    assert t["interpretation"] and not t["warnings"]
    q["reference_date"] = "2026-09-23"
    res = client.post("/api/search", json={"query": q}).json()
    assert {p["patient_id"] for p in res["matched"]} == {"P0003070", "P0003091", "P0003094"}
    assert {p["patient_id"] for p in res["unassessable"]} == {"P0003088"}


@live
def test_translate_family_history_query():
    r = client.post("/api/translate", json={"text": "female, born 1965–2005, with a first- or second-degree relative with breast cancer"})
    assert r.status_code == 200, r.text
    q = r.json()["query"]
    rel = next(c for c in q["criteria"] if c["kind"] == "relative")
    assert (rel.get("degree_min", 1), rel["degree_max"]) == (1, 2)
    assert rel["where"]["term"]["id"] == "HP:0003002"
    res = client.post("/api/search", json={"query": q}).json()
    assert res["counts"]["matched"] == 11 and res["counts"]["unassessable"] == 3
