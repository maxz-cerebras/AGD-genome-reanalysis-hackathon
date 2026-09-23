"""Load family phenopackets and derive pedigree relationships.

Relationship degree is not stored in the export; it is computed here from
``pedigree.persons`` parent links using the clinical convention
(degree = -log2 of the coefficient of relationship): parents, children and
full siblings are 1st degree; grandparents, aunts/uncles, half-siblings are
2nd degree; first cousins are 3rd degree.
"""

from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Feature:
    id: str
    label: str
    negated: bool
    path: str


@dataclass(frozen=True)
class Gene:
    id: str
    symbol: str
    path: str


@dataclass(frozen=True)
class Disease:
    id: str
    label: str
    path: str

    @property
    def evidence(self) -> str:
        """'molecular' for MIM (final diagnosis), 'clinical' for ORDO, else 'other'."""
        if self.id.startswith("MIM:"):
            return "molecular"
        if self.id.startswith("ORDO:"):
            return "clinical"
        return "other"


@dataclass
class Individual:
    id: str
    family_id: str
    path: str  # "proband" or "relatives[i]"
    sex: str | None
    birth_year: int | None
    father_id: str | None
    mother_id: str | None
    features: list[Feature] = field(default_factory=list)
    genes: list[Gene] = field(default_factory=list)
    diseases: list[Disease] = field(default_factory=list)

    @property
    def is_proband(self) -> bool:
        return self.path == "proband"

    @property
    def dob_path(self) -> str:
        return f"{self.path}.subject.dateOfBirth"

    @property
    def sex_path(self) -> str:
        return f"{self.path}.subject.sex"


@dataclass(frozen=True)
class Relation:
    """How ``other`` relates to the index individual (e.g. "mother", 2nd degree, maternal)."""

    other_id: str
    degree: int | None  # None: no shared ancestry (partner / unrelated)
    label: str  # human label from the index person's point of view
    roles: frozenset[str]  # coarse roles for matching: parent, mother, sibling, ...
    lineage: str | None  # "maternal" | "paternal" | None
    generation: int  # relative to index person: -1 parents, +1 children
    traversal: str = ""  # the pedigree.persons link chain, e.g. "P1 →maternalId→ 0 →maternalId→ 3"


class Family:
    def __init__(self, raw: dict):
        self.raw = raw
        self.id: str = raw["id"]
        self.members: dict[str, Individual] = {}
        persons = {p["individualId"]: p for p in raw.get("pedigree", {}).get("persons", [])}
        self.proband = self._individual(raw["proband"], "proband", persons)
        self.members[self.proband.id] = self.proband
        for i, rel in enumerate(raw.get("relatives", [])):
            ind = self._individual(rel, f"relatives[{i}]", persons)
            self.members[ind.id] = ind
        # pedigree-only persons (none in this export, but keep the graph complete)
        for pid, p in persons.items():
            if pid not in self.members:
                self.members[pid] = Individual(
                    pid, self.id, f"pedigree.persons[{pid}]", p.get("sex"), None,
                    p.get("paternalId"), p.get("maternalId"),
                )
        self._children: dict[str, set[str]] = {m: set() for m in self.members}
        for m in self.members.values():
            for parent in (m.father_id, m.mother_id):
                if parent in self._children:
                    self._children[parent].add(m.id)

    def _individual(self, pp: dict, path: str, persons: dict) -> Individual:
        subj = pp["subject"]
        ped = persons.get(subj["id"], {})
        dob = subj.get("dateOfBirth")
        ind = Individual(
            id=subj["id"], family_id=self.id, path=path,
            sex=subj.get("sex") or ped.get("sex"),
            birth_year=int(dob[:4]) if dob else None,
            father_id=ped.get("paternalId"), mother_id=ped.get("maternalId"),
        )
        for i, ft in enumerate(pp.get("phenotypicFeatures", [])):
            ind.features.append(Feature(ft["type"]["id"], ft["type"].get("label", ""),
                                        bool(ft.get("negated")), f"{path}.phenotypicFeatures[{i}]"))
        for i, g in enumerate(pp.get("genes", [])):
            ind.genes.append(Gene(g.get("id", ""), g.get("symbol", ""), f"{path}.genes[{i}]"))
        for i, d in enumerate(pp.get("diseases", [])):
            ind.diseases.append(Disease(d["term"]["id"], d["term"].get("label", ""), f"{path}.diseases[{i}]"))
        return ind

    # -- pedigree graph -----------------------------------------------------

    def parents(self, pid: str) -> list[str]:
        m = self.members[pid]
        return [p for p in (m.father_id, m.mother_id) if p in self.members]

    def children(self, pid: str) -> set[str]:
        return self._children[pid]

    def ancestors(self, pid: str) -> dict[str, int]:
        """Ancestor id -> generational distance (self included at 0)."""
        dist = {pid: 0}
        q = deque([pid])
        while q:
            cur = q.popleft()
            for p in self.parents(cur):
                if p not in dist:
                    dist[p] = dist[cur] + 1
                    q.append(p)
        return dist

    @cached_property
    def relations(self) -> dict[str, Relation]:
        """Every family member described relative to the proband."""
        return {m: self.relation(self.proband.id, m) for m in self.members if m != self.proband.id}

    def relatives(self) -> Iterator[tuple[Individual, Relation]]:
        for mid, rel in self.relations.items():
            yield self.members[mid], rel

    def relation(self, index: str, other: str) -> Relation:
        anc_x = self.ancestors(index)
        anc_y = self.ancestors(other)
        common = set(anc_x) & set(anc_y)
        # keep only the closest common ancestors: drop any that is an ancestor of another one
        minimal = {a for a in common if not any(b != a and a in self.ancestors(b) for b in common)}
        generation = self._generation(index, other)
        if not minimal:
            partner = bool(self.children(index) & self.children(other))
            return Relation(other, None, "partner" if partner else "unrelated",
                            frozenset({"partner"} if partner else {"unrelated"}), None, generation,
                            f"{index} and {other} share a child; no parent link between them" if partner else "no pedigree path")
        r = sum(0.5 ** (anc_x[a] + anc_y[a]) for a in minimal)
        degree = round(-math.log2(r))
        dx = min(anc_x[a] for a in minimal)
        dy = min(anc_y[a] for a in minimal)
        full = len(minimal) >= 2
        sex = self.members[other].sex
        label, roles = _name(dx, dy, full, sex, degree)
        lineage = None
        if dx > 0:
            m = self.members[index]
            via = {
                side for side, pid in (("maternal", m.mother_id), ("paternal", m.father_id))
                if pid in self.members and minimal & set(self.ancestors(pid))
            }
            if len(via) == 1:
                lineage = via.pop()
        return Relation(other, degree, label, frozenset(roles), lineage, generation, self._traversal(index, other, minimal, anc_x, anc_y))

    def _traversal(self, index: str, other: str, minimal: set[str], anc_x: dict, anc_y: dict) -> str:
        """Human-readable parent-link chain from index up to a closest common ancestor and down to other."""
        top = min(minimal, key=lambda a: (anc_x[a] + anc_y[a], a))

        def up(start: str) -> list[str]:  # ids from start to top, following the parent that leads to top
            chain = [start]
            while chain[-1] != top:
                nxt = next(p for p in self.parents(chain[-1]) if top in self.ancestors(p))
                chain.append(nxt)
            return chain

        def link(child: str, parent: str) -> str:
            return "maternalId" if self.members[child].mother_id == parent else "paternalId"

        up_x, up_y = up(index), up(other)
        parts = [index]
        for c, p in zip(up_x, up_x[1:]):
            parts.append(f"→{link(c, p)}→ {p}")
        for p, c in zip(reversed(up_y), list(reversed(up_y))[1:]):
            parts.append(f"←{link(c, p)}← {c}")
        return " ".join(parts)

    def _generation(self, index: str, other: str) -> int:
        """Generation offset via parent(-1)/child(+1)/co-parent(0) edges (BFS)."""
        seen = {index: 0}
        q = deque([index])
        while q:
            cur = q.popleft()
            if cur == other:
                return seen[cur]
            nxt = [(p, -1) for p in self.parents(cur)] + [(c, 1) for c in self.children(cur)]
            for c in self.children(cur):
                nxt += [(p, 0) for p in self.parents(c) if p != cur]
            for n, d in nxt:
                if n not in seen:
                    seen[n] = seen[cur] + d
                    q.append(n)
        return 0

    def generations(self) -> dict[str, int]:
        return {m: self._generation(self.proband.id, m) for m in self.members}


def _sexed(sex: str | None, female: str, male: str, neutral: str) -> str:
    return {"FEMALE": female, "MALE": male}.get(sex or "", neutral)


def _name(dx: int, dy: int, full: bool, sex: str | None, degree: int) -> tuple[str, set[str]]:
    great = lambda n: "great-" * n  # noqa: E731
    if dy == 0:  # other is an ancestor of index
        if dx == 1:
            return _sexed(sex, "mother", "father", "parent"), {"parent", _sexed(sex, "mother", "father", "parent")}
        base = _sexed(sex, "grandmother", "grandfather", "grandparent")
        return great(dx - 2) + base, {"grandparent", "ancestor"}
    if dx == 0:  # other is a descendant of index
        if dy == 1:
            return _sexed(sex, "daughter", "son", "child"), {"child", _sexed(sex, "daughter", "son", "child")}
        base = _sexed(sex, "granddaughter", "grandson", "grandchild")
        return great(dy - 2) + base, {"grandchild", "descendant"}
    half = "" if full else "half-"
    if dx == 1 and dy == 1:
        base = _sexed(sex, "sister", "brother", "sibling")
        roles = {"sibling", base} if full else {"half_sibling"}
        return half + base, roles
    if dy == 1:  # other is a sibling of an ancestor
        base = _sexed(sex, "aunt", "uncle", "aunt/uncle")
        return half + great(dx - 2) + base, {"aunt_uncle", _sexed(sex, "aunt", "uncle", "aunt_uncle")}
    if dx == 1:  # other is a descendant of a sibling
        base = _sexed(sex, "niece", "nephew", "niece/nephew")
        return half + great(dy - 2) + base, {"niece_nephew", _sexed(sex, "niece", "nephew", "niece_nephew")}
    n = min(dx, dy) - 1
    removed = abs(dx - dy)
    ordinal = {1: "first", 2: "second", 3: "third"}.get(n, f"{n}th")
    label = f"{half}{ordinal} cousin" + (f" {['', 'once', 'twice'][removed] if removed < 3 else removed} removed" if removed else "")
    return label, {"cousin"}


class Registry:
    def __init__(self, families: list[Family]):
        self.families = families
        self.by_proband = {f.proband.id: f for f in families}

    @classmethod
    def load(cls, path: str | Path) -> "Registry":
        with open(path) as fh:
            return cls([Family(json.loads(line)) for line in fh if line.strip()])

    def individuals(self) -> Iterator[Individual]:
        for f in self.families:
            yield from f.members.values()

    @cached_property
    def vocabulary(self) -> dict:
        """Distinct diseases, genes and HPO terms present in the registry (for LLM grounding)."""
        diseases: dict[str, str] = {}
        genes: dict[str, str] = {}
        hpo: dict[str, str] = {}
        for ind in self.individuals():
            for d in ind.diseases:
                diseases.setdefault(d.id, d.label)
            for g in ind.genes:
                genes.setdefault(g.symbol, g.id)
            for f in ind.features:
                hpo.setdefault(f.id, f.label)
        return {"diseases": diseases, "genes": genes, "hpo": hpo}
