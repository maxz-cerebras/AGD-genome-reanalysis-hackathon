from kinquery.query import Query

LYNCH = [{"id": "ORDO:144"}, {"id": "MIM:120435"}, {"id": "MIM:609310"}, {"id": "MIM:614350"}, {"id": "MIM:614337"}]


def q(*criteria, **kw):
    return Query.model_validate({"criteria": list(criteria), **kw})


def ids(bucket):
    return {p["patient_id"] for p in bucket}


def test_hpo_descendants_match(matcher, hpo):
    # data has "Generalized myoclonic seizure" etc. as children of Seizure; label match alone would miss them
    with_desc = matcher.search(q({"kind": "phenotype", "term": {"id": "HP:0001250"}}))
    exact = matcher.search(q({"kind": "phenotype", "term": {"id": "HP:0001250"}, "include_descendants": False}))
    assert with_desc["counts"]["matched"] > exact["counts"]["matched"]
    p = with_desc["matched"][0]
    assert p["outcomes"][0]["evidence"][0]["field"].startswith("proband.phenotypicFeatures[")


def test_negated_feature_is_excluded_not_present(matcher, registry):
    neg = next((ind, f) for ind in registry.individuals() for f in ind.features if f.negated and ind.is_proband)
    ind, f = neg
    present = matcher.search(q({"kind": "phenotype", "term": {"id": f.id}, "include_descendants": False}))
    excluded = matcher.search(q({"kind": "phenotype", "term": {"id": f.id}, "status": "excluded", "include_descendants": False}))
    absent = matcher.search(q({"kind": "phenotype", "term": {"id": f.id}, "status": "absent", "include_descendants": False}))
    assert ind.id not in ids(present["matched"])
    assert ind.id in ids(excluded["matched"])
    assert ind.id not in ids(absent["matched"])


def test_missing_dob_is_unassessable_not_excluded(matcher, registry):
    no_dob = {f.proband.id for f in registry.families if f.proband.birth_year is None}
    assert len(no_dob) == 20
    res = matcher.search(q({"kind": "birth_year", "min": 1965, "max": 2005}))
    assert ids(res["unassessable"]) == no_dob
    assert res["unassessable"][0]["outcomes"][0]["summary"] == "no dateOfBirth recorded"


def test_kleene_logic_with_not_and_any(matcher):
    # NOT(unknown) stays unknown; ANY(met, unknown) is met
    res = matcher.search(q({"kind": "not", "criterion": {"kind": "age", "max": 10}}, reference_date="2026-01-01"))
    assert res["counts"]["unassessable"] == 20
    res = matcher.search(q({"kind": "any", "criteria": [{"kind": "age", "max": 10}, {"kind": "sex", "value": "FEMALE"}]},
                           reference_date="2026-01-01"))
    assert all(p["sex"] == "FEMALE" or p["birth_year"] for p in res["matched"])
    assert all(p["sex"] == "MALE" and p["birth_year"] is None for p in res["unassessable"])


def test_diagnosis_evidence_kinds(matcher):
    both = matcher.search(q({"kind": "diagnosis", "terms": LYNCH}))
    mol = matcher.search(q({"kind": "diagnosis", "terms": LYNCH, "evidence": ["molecular"]}))
    clin = matcher.search(q({"kind": "diagnosis", "terms": LYNCH, "evidence": ["clinical"]}))
    assert ids(both["matched"]) == ids(mol["matched"]) | ids(clin["matched"])
    assert clin["counts"]["matched"] >= 1 and mol["counts"]["matched"] >= 1
    # a gene finding alone is not a diagnosis
    gene_only = next(f.proband for f in matcher.registry.families if f.proband.genes and not f.proband.diseases)
    sym = gene_only.genes[0].symbol
    gene = matcher.search(q({"kind": "gene", "symbols": [sym]}))
    anydx = matcher.search(q({"kind": "diagnosis"}))
    assert gene_only.id in ids(gene["matched"]) and gene_only.id not in ids(anydx["matched"])


def test_relative_degree_and_roles(matcher, registry):
    bc = {"kind": "phenotype", "term": {"id": "HP:0003002"}}
    first = matcher.search(q({"kind": "relative", "degree_max": 1, "where": bc}))
    second = matcher.search(q({"kind": "relative", "degree_max": 2, "where": bc}))
    mother = matcher.search(q({"kind": "relative", "roles": ["mother"], "where": bc}))
    assert ids(mother["matched"]) <= ids(first["matched"]) <= ids(second["matched"])
    assert len(ids(second["matched"])) > len(ids(first["matched"]))
    p = next(p for p in second["matched"] if p["patient_id"] == "P0003002")
    kid = p["outcomes"][0]["children"][0]
    assert kid["relation"] == "aunt (2nd degree, paternal)"
    assert kid["evidence"][0]["field"] == "pedigree.persons[individualId=4]"
    assert kid["evidence"][1]["field"] == "relatives[4].phenotypicFeatures[0]"


def test_relative_min_count_and_unknown_propagation(matcher):
    two = matcher.search(q({"kind": "relative", "min_count": 2,
                            "where": {"kind": "has_record", "of": ["phenotype", "diagnosis"]}}))
    one = matcher.search(q({"kind": "relative", "min_count": 1,
                            "where": {"kind": "has_record", "of": ["phenotype", "diagnosis"]}}))
    assert ids(two["matched"]) < ids(one["matched"])
    # relatives mostly lack DOB: an age criterion on relatives produces unassessable, not unmet
    res = matcher.search(q({"kind": "relative", "where": {"kind": "age", "min": 60}}, reference_date="2026-01-01"))
    assert res["counts"]["unassessable"] > 0


def test_challenge_example_child_seizures_no_molecular_dx(matcher):
    res = matcher.search(q(
        {"kind": "age", "max": 10},
        {"kind": "phenotype", "term": {"id": "HP:0001250"}},
        {"kind": "not", "criterion": {"kind": "diagnosis", "evidence": ["molecular"]}},
        reference_date="2026-09-23",
    ))
    assert ids(res["matched"]) == {"P0003070", "P0003091", "P0003094"}
    assert ids(res["unassessable"]) == {"P0003088"}
    p70 = next(p for p in res["matched"] if p["patient_id"] == "P0003070")
    assert "ORDO:1606" in p70["outcomes"][2]["summary"]  # clinical dx present, molecular absent: still qualifies
