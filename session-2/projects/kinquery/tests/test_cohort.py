"""Protocol-shaped semantics: exclusions, at-least-N groups, grounding of hallucinated HPO ids."""
from kinquery.nlq import Translation, Translator
from kinquery.query import Query

NEURO = [{"kind": "phenotype", "term": {"id": t}} for t in ("HP:0001250", "HP:0001249", "HP:0001263", "HP:0001252")]


def q(criteria, exclusions=(), **kw):
    return Query.model_validate({"criteria": criteria, "exclusions": list(exclusions), **kw})


def ids(bucket):
    return {p["patient_id"] for p in bucket}


def test_at_least_n_of_is_stricter_than_any(matcher):
    any_ = matcher.search(q([{"kind": "any", "criteria": NEURO}]))
    two = matcher.search(q([{"kind": "any", "min_count": 2, "criteria": NEURO}]))
    assert ids(two["matched"]) < ids(any_["matched"])
    p = two["matched"][0]
    assert sum(k["status"] == "met" for k in p["outcomes"][0]["children"]) >= 2
    assert "need at least 2" in p["outcomes"][0]["summary"]


def test_exclusion_removes_and_unknown_exclusion_is_unassessable(matcher):
    base = matcher.search(q([{"kind": "phenotype", "term": {"id": "HP:0003002"}}]))
    excl = matcher.search(q([{"kind": "phenotype", "term": {"id": "HP:0003002"}}],
                            [{"kind": "diagnosis", "evidence": ["molecular"]}]))
    assert ids(excl["matched"]) < ids(base["matched"])
    assert all(o["status"] == "unmet" for p in excl["matched"] for o in p["exclusion_outcomes"])
    n_molecular = sum(1 for f in matcher.registry.families if any(d.evidence == "molecular" for d in f.proband.diseases))
    assert excl["funnel"]["exclusion"][0] == n_molecular  # funnel counts are independent, over all patients
    # exclusion "over 60" on a patient without DOB: cannot confirm eligibility
    age = matcher.search(q([{"kind": "sex", "value": "FEMALE"}], [{"kind": "age", "min": 60}], reference_date="2026-01-01"))
    assert all(p["birth_year"] is None for p in age["unassessable"]) and age["unassessable"]


def test_grounding_label_wins_over_hallucinated_id(registry, hpo):
    tr = Translator.__new__(Translator)
    tr.registry, tr.hpo = registry, hpo
    # right label, wrong id -> id replaced, noted
    t = Translation(q([{"kind": "phenotype", "term": {"id": "HP:0010582", "label": "Seizure"}}]), "")
    tr.ground(t)
    assert t.query.criteria[0].term.id == "HP:0001250" and t.notes and not t.warnings
    # label with no exact HPO match -> loud warning with candidates, id kept as given
    t = Translation(q([{"kind": "phenotype", "term": {"id": "HP:0010582", "label": "Colonic polyposis"}}]), "")
    tr.ground(t)
    assert t.query.criteria[0].term.id == "HP:0010582"
    assert t.warnings and "HP:0005227" in t.warnings[0]
    # exclusions are grounded too
    t = Translation(q([{"kind": "sex", "value": "MALE"}], [{"kind": "phenotype", "term": {"id": "HP:9999999", "label": "Hypotonia"}}]), "")
    tr.ground(t)
    assert t.query.exclusions[0].term.id == "HP:0001252"
