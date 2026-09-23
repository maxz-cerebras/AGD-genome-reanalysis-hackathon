"""Evaluate a structured Query against the registry.

Every criterion yields ``met`` / ``unmet`` / ``unknown`` plus the exact
phenopacket fields that decided it. ``unknown`` means the record lacks the data
needed (e.g. no dateOfBirth for an age criterion); it propagates through
AND/OR/NOT with Kleene logic so a patient is reported as *unassessable* rather
than silently excluded.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Literal

from .hpo import HPO
from .query import (
    AgeCriterion, BirthYearCriterion, DiagnosisCriterion, GeneCriterion, GroupCriterion,
    HasRecordCriterion, NotCriterion, PhenotypeCriterion, Query, RelativeCriterion, SexCriterion,
)
from .registry import Family, Individual, Registry, Relation

Status = Literal["met", "unmet", "unknown"]
ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}


def describe(rel: Relation) -> str:
    if rel.degree is None:
        return rel.label
    deg = ORDINAL.get(rel.degree, f"{rel.degree}th") + " degree"
    return f"{rel.label} ({deg}{', ' + rel.lineage if rel.lineage else ''})"


@dataclass
class Evidence:
    individual: str
    relation: str  # "self" or e.g. "mother (1st degree, maternal)"
    field: str  # JSON path inside the family record
    value: str


@dataclass
class Outcome:
    status: Status
    criterion: dict
    summary: str
    subject: str  # individual id the criterion was evaluated on
    relation: str
    evidence: list[Evidence] = field(default_factory=list)
    children: list["Outcome"] = field(default_factory=list)


def _all(statuses: list[Status]) -> Status:
    if "unmet" in statuses:
        return "unmet"
    if "unknown" in statuses:
        return "unknown"
    return "met"


def _any(statuses: list[Status]) -> Status:
    if "met" in statuses:
        return "met"
    if "unknown" in statuses:
        return "unknown"
    return "unmet"


class Matcher:
    def __init__(self, registry: Registry, hpo: HPO):
        self.registry = registry
        self.hpo = hpo

    # -- public -------------------------------------------------------------

    def search(self, query: Query) -> dict:
        ref_year = date.fromisoformat(query.reference_date).year if query.reference_date else date.today().year
        buckets: dict[str, list[dict]] = {"met": [], "unknown": [], "unmet": []}
        n_inc, n_exc = len(query.criteria), len(query.exclusions)
        funnel = {"inclusion": [0] * n_inc, "exclusion": [0] * n_exc}
        for fam in self.registry.families:
            p = fam.proband
            inc = [self.evaluate(c, fam, p, "self", ref_year) for c in query.criteria]
            exc = [self.evaluate(c, fam, p, "self", ref_year) for c in query.exclusions]
            # an exclusion that holds -> unmet; unknown stays unknown (cannot confirm the patient is eligible)
            flipped = [{"met": "unmet", "unmet": "met", "unknown": "unknown"}[o.status] for o in exc]
            status = _all([o.status for o in inc] + flipped)
            for i, o in enumerate(inc):
                funnel["inclusion"][i] += o.status == "met"
            for i, o in enumerate(exc):
                funnel["exclusion"][i] += o.status == "met"
            buckets[status].append({
                "patient_id": p.id, "family_id": fam.id, "sex": p.sex, "birth_year": p.birth_year,
                "status": status,
                "outcomes": [asdict(o) for o in inc],
                "exclusion_outcomes": [asdict(o) for o in exc],
            })
        return {
            "reference_year": ref_year,
            "counts": {"matched": len(buckets["met"]), "unassessable": len(buckets["unknown"]),
                       "unmatched": len(buckets["unmet"]), "total": len(self.registry.families)},
            "funnel": funnel,  # patients satisfying each inclusion / triggering each exclusion, independently
            "matched": buckets["met"],
            "unassessable": buckets["unknown"],
            "unmatched": buckets["unmet"],
        }

    # -- evaluation ---------------------------------------------------------

    def evaluate(self, c, fam: Family, ind: Individual, relation: str, ref_year: int) -> Outcome:
        crit = c.model_dump(exclude_none=True)

        def out(status: Status, summary: str, evidence: list[Evidence] = (), children: list[Outcome] = ()) -> Outcome:
            return Outcome(status, crit, summary, ind.id, relation, list(evidence), list(children))

        ev = lambda fld, value: Evidence(ind.id, relation, fld, value)  # noqa: E731

        if isinstance(c, SexCriterion):
            if not ind.sex:
                return out("unknown", "sex not recorded")
            ok = ind.sex == c.value
            return out("met" if ok else "unmet", f"sex is {ind.sex}", [ev(ind.sex_path, ind.sex)])

        if isinstance(c, BirthYearCriterion):
            if ind.birth_year is None:
                return out("unknown", "no dateOfBirth recorded")
            ok = (c.min is None or ind.birth_year >= c.min) and (c.max is None or ind.birth_year <= c.max)
            return out("met" if ok else "unmet", f"born {ind.birth_year}", [ev(ind.dob_path, str(ind.birth_year))])

        if isinstance(c, AgeCriterion):
            if ind.birth_year is None:
                return out("unknown", "no dateOfBirth recorded, age cannot be computed")
            age = ref_year - ind.birth_year
            ok = (c.min is None or age >= c.min) and (c.max is None or age < c.max)
            return out("met" if ok else "unmet", f"age {age} in {ref_year} (year precision)",
                       [ev(ind.dob_path, f"{ind.birth_year} -> age {age}")])

        if isinstance(c, PhenotypeCriterion):
            return self._phenotype(c, ind, out, ev)

        if isinstance(c, GeneCriterion):
            wanted = {s.upper() for s in c.symbols}
            hits = [g for g in ind.genes if g.symbol.upper() in wanted or g.id in wanted]
            if hits:
                return out("met", "gene finding " + ", ".join(g.symbol for g in hits),
                           [ev(g.path, f"{g.symbol} ({g.id})") for g in hits])
            return out("unmet", "no finding in " + "/".join(sorted(wanted)))

        if isinstance(c, DiagnosisCriterion):
            ids = {t.id for t in c.terms} if c.terms else None
            hits = [d for d in ind.diseases if d.evidence in c.evidence and (ids is None or d.id in ids)]
            if hits:
                return out("met", "diagnosis " + "; ".join(f"{d.label} [{d.evidence}]" for d in hits),
                           [ev(d.path, f"{d.id} {d.label} ({d.evidence})") for d in hits])
            what = "any disease" if ids is None else "listed diseases"
            near = [d for d in ind.diseases if ids is None or d.id in ids]
            note = f"; only {near[0].evidence} diagnosis {near[0].id} present" if near else ""
            return out("unmet", f"no {'/'.join(c.evidence)} diagnosis of {what}{note}",
                       [ev(d.path, f"{d.id} {d.label} ({d.evidence})") for d in near])

        if isinstance(c, HasRecordCriterion):
            evidence: list[Evidence] = []
            if "phenotype" in c.of:
                evidence += [ev(f.path, f"{f.id} {f.label}") for f in ind.features if not f.negated]
            if "diagnosis" in c.of:
                evidence += [ev(d.path, f"{d.id} {d.label}") for d in ind.diseases]
            if "gene" in c.of:
                evidence += [ev(g.path, g.symbol) for g in ind.genes]
            if evidence:
                return out("met", f"{len(evidence)} recorded item(s)", evidence)
            return out("unmet", "nothing recorded for " + "/".join(c.of))

        if isinstance(c, NotCriterion):
            inner = self.evaluate(c.criterion, fam, ind, relation, ref_year)
            flipped: Status = {"met": "unmet", "unmet": "met", "unknown": "unknown"}[inner.status]
            return out(flipped, f"NOT ({inner.summary})", inner.evidence, [inner])

        if isinstance(c, GroupCriterion):
            kids = [self.evaluate(k, fam, ind, relation, ref_year) for k in c.criteria]
            n = sum(k.status == "met" for k in kids)
            n_unknown = sum(k.status == "unknown" for k in kids)
            if c.kind == "any" and c.min_count:
                status: Status = "met" if n >= c.min_count else "unknown" if n + n_unknown >= c.min_count else "unmet"
                return out(status, f"{n}/{len(kids)} criteria met (need at least {c.min_count})", [], kids)
            status = (_all if c.kind == "all" else _any)([k.status for k in kids])
            return out(status, f"{n}/{len(kids)} criteria met ({c.kind})", [], kids)

        if isinstance(c, RelativeCriterion):
            return self._relative(c, fam, ind, out, ref_year)

        raise TypeError(f"unsupported criterion {type(c).__name__}")

    def _phenotype(self, c: PhenotypeCriterion, ind: Individual, out, ev) -> Outcome:
        tid = c.term.id
        scope = self.hpo.descendants(tid) if c.include_descendants and tid in self.hpo else {tid}
        label = self.hpo.label.get(tid, c.term.label or tid)
        in_scope = [f for f in ind.features if f.id in scope]
        present = [f for f in in_scope if not f.negated]
        negated = [f for f in in_scope if f.negated]
        def fmt(f):
            how = " (negated)" if f.negated else ""
            if f.id != tid:
                how += f" — HPO descendant of {tid} {label}"
            return ev(f.path, f"{f.id} {f.label}{how}")
        if c.status == "present":
            if present:
                return out("met", f"{label}: " + ", ".join(f.label for f in present), [fmt(f) for f in present])
            note = "; recorded as excluded" if negated else ""
            return out("unmet", f"no {label} recorded{note}", [fmt(f) for f in negated])
        if c.status == "excluded":
            if negated:
                return out("met", f"{label} explicitly excluded", [fmt(f) for f in negated])
            return out("unmet", f"{label} not recorded as excluded", [fmt(f) for f in present])
        # absent: nothing about the term at all
        if in_scope:
            return out("unmet", f"{label} is recorded", [fmt(f) for f in in_scope])
        return out("met", f"no record of {label}")

    def _relative(self, c: RelativeCriterion, fam: Family, ind: Individual, out, ref_year: int) -> Outcome:
        candidates: list[tuple[Individual, Relation]] = []
        for mid, other in fam.members.items():
            if mid == ind.id:
                continue
            rel = fam.relation(ind.id, mid)
            if rel.degree is None:
                continue
            if c.degree_min is not None and rel.degree < c.degree_min:
                continue
            if c.degree_max is not None and rel.degree > c.degree_max:
                continue
            if c.roles and not (set(c.roles) & rel.roles):
                continue
            if c.lineage and rel.lineage != c.lineage:
                continue
            if c.sex and other.sex != c.sex:
                continue
            candidates.append((other, rel))

        kids: list[Outcome] = []
        for other, rel in candidates:
            desc = describe(rel)
            link = Evidence(other.id, desc, f"pedigree.persons[individualId={other.id}]", f"{desc}; traversal: {rel.traversal}")
            if c.where is None:
                kids.append(Outcome("met", {"kind": "relative_exists"}, desc, other.id, desc, [link]))
                continue
            o = self.evaluate(c.where, fam, other, desc, ref_year)
            if o.status != "unmet":
                o.evidence.insert(0, link)
                kids.append(o)
        n_met = sum(k.status == "met" for k in kids)
        n_unknown = len(kids) - n_met
        if n_met >= c.min_count:
            status: Status = "met"
        elif n_met + n_unknown >= c.min_count:
            status = "unknown"
        else:
            status = "unmet"
        scope = _scope(c)
        summary = f"{n_met} of {len(candidates)} {scope} matched"
        if n_unknown:
            summary += f", {n_unknown} could not be assessed"
        if c.min_count > 1:
            summary += f" (need {c.min_count})"
        return out(status, summary, [], kids)


def _scope(c: RelativeCriterion) -> str:
    if c.roles:
        who = "/".join(c.roles)
    elif c.degree_max is not None:
        who = f"relatives of degree {c.degree_min or 1}-{c.degree_max}"
    else:
        who = "relatives"
    if c.lineage:
        who = f"{c.lineage} {who}"
    if c.sex:
        who = f"{c.sex.lower()} {who}"
    return who
