from kinquery.registry import Family


def _fam(persons, sexes=None):
    sexes = sexes or {}
    raw = {
        "id": "F",
        "proband": {"id": "u", "subject": {"id": "P", "sex": sexes.get("P", "FEMALE")}},
        "relatives": [{"id": f"u{p}", "subject": {"id": p, "sex": sexes.get(p, "FEMALE")}} for p in persons if p != "P"],
        "pedigree": {"persons": [
            {"familyId": "F", "individualId": p, "sex": sexes.get(p, "FEMALE"),
             **({"paternalId": persons[p][0]} if persons[p] and persons[p][0] else {}),
             **({"maternalId": persons[p][1]} if persons[p] and persons[p][1] else {})}
            for p in persons
        ]},
    }
    return Family(raw)


# proband P; parents F,M; paternal grandparents GF,GM; father's brother U (full) and half-sister H (via GF and X);
# U's daughter C (cousin); P's full sister S, half-brother HB (father + other woman W); P's daughter D with partner Z.
PED = {
    "P": ("F", "M"), "F": ("GF", "GM"), "M": None, "GF": None, "GM": None,
    "U": ("GF", "GM"), "X": None, "H": ("GF", "X"), "C": ("U", None),
    "S": ("F", "M"), "W": None, "HB": ("F", "W"), "Z": None, "D": ("Z", "P"),
}
SEX = {"F": "MALE", "GF": "MALE", "U": "MALE", "HB": "MALE", "Z": "MALE"}


def test_degrees_and_labels():
    fam = _fam(PED, SEX)
    rel = fam.relations
    expect = {
        "F": (1, "father", "paternal"), "M": (1, "mother", "maternal"),
        "GF": (2, "grandfather", "paternal"), "GM": (2, "grandmother", "paternal"),
        "U": (2, "uncle", "paternal"), "H": (3, "half-aunt", "paternal"),
        "C": (3, "first cousin", "paternal"),
        "S": (1, "sister", None), "HB": (2, "half-brother", "paternal"),
        "D": (1, "daughter", None), "Z": (None, "partner", None),
        "W": (None, "unrelated", None), "X": (None, "unrelated", None),
    }
    got = {k: (r.degree, r.label, r.lineage) for k, r in rel.items()}
    assert got == expect


def test_roles_and_generations():
    fam = _fam(PED, SEX)
    assert {"parent", "father"} <= fam.relations["F"].roles
    assert fam.relations["HB"].roles == {"half_sibling"}
    assert fam.relations["C"].roles == {"cousin"}
    gens = fam.generations()
    assert gens["GF"] == -2 and gens["U"] == -1 and gens["C"] == 0 and gens["D"] == 1 and gens["Z"] == 0


def test_real_data_loads(registry):
    assert len(registry.families) == 100
    assert sum(len(f.members) for f in registry.families) == 629
    fam = registry.by_proband["P0003001"]
    assert fam.relations["0"].label == "mother"
    assert fam.relations["4"].label == "aunt" and fam.relations["4"].degree == 2
    assert fam.members["0"].features[0].path == "relatives[0].phenotypicFeatures[0]"
