"""Structured cohort query.

The LLM translates free text into this schema; the UI renders it for
confirmation; the matcher evaluates it. Criteria categories:

* demographics       - ``sex``, ``birth_year``, ``age``
* phenotype          - ``phenotype`` (HPO term, subsumption, present / excluded / absent)
* gene finding       - ``gene``
* diagnosis          - ``diagnosis`` (molecular ``MIM:`` vs clinical ``ORDO:``)
* any recorded data  - ``has_record``
* family history     - ``relative`` (degree, role, lineage, count, nested criteria)
* logic              - ``all``, ``any``, ``not``
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

Sex = Literal["FEMALE", "MALE"]
Evidence = Literal["molecular", "clinical"]
Role = Literal[
    "parent", "mother", "father",
    "sibling", "sister", "brother", "half_sibling",
    "child", "daughter", "son",
    "grandparent", "grandchild",
    "aunt_uncle", "aunt", "uncle",
    "niece_nephew", "niece", "nephew",
    "cousin",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Term(Strict):
    id: str = Field(description="Ontology id, e.g. HP:0001250, MIM:120435, ORDO:144")
    label: str | None = Field(default=None, description="Human-readable label")


class SexCriterion(Strict):
    kind: Literal["sex"]
    value: Sex


class BirthYearCriterion(Strict):
    kind: Literal["birth_year"]
    min: int | None = Field(default=None, description="Earliest birth year, inclusive")
    max: int | None = Field(default=None, description="Latest birth year, inclusive")


class AgeCriterion(Strict):
    kind: Literal["age"]
    min: int | None = Field(default=None, description="Minimum age in years, inclusive")
    max: int | None = Field(default=None, description="Maximum age in years, EXCLUSIVE ('under 10' -> max=10)")


class PhenotypeCriterion(Strict):
    kind: Literal["phenotype"]
    term: Term = Field(description="HPO term")
    include_descendants: bool = Field(default=True, description="Match any HPO descendant (e.g. focal seizure for Seizure)")
    status: Literal["present", "excluded", "absent"] = Field(
        default="present",
        description="present: observed; excluded: recorded as negated (looked for, found absent); absent: no record of the term at all",
    )


class GeneCriterion(Strict):
    kind: Literal["gene"]
    symbols: list[str] = Field(description="Gene symbols; any one matches (e.g. ['BRCA1','BRCA2'])")


class DiagnosisCriterion(Strict):
    kind: Literal["diagnosis"]
    terms: list[Term] | None = Field(default=None, description="Disease terms; any one matches. null = any disease")
    evidence: list[Evidence] = Field(
        default=["molecular", "clinical"],
        description="molecular = MIM: (final molecular diagnosis); clinical = ORDO: (clinical diagnosis)",
    )


class HasRecordCriterion(Strict):
    kind: Literal["has_record"]
    of: list[Literal["phenotype", "diagnosis", "gene"]] = Field(
        description="Any recorded (non-negated) phenotype, any diagnosis, or any gene finding"
    )


class NotCriterion(Strict):
    kind: Literal["not"]
    criterion: "Criterion"


class GroupCriterion(Strict):
    kind: Literal["all", "any"]
    criteria: list["Criterion"] = Field(min_length=1)
    min_count: int | None = Field(
        default=None, ge=1,
        description="For kind 'any': at least this many of the criteria must hold ('at least two of the following' -> 2)",
    )


class RelativeCriterion(Strict):
    kind: Literal["relative"]
    degree_min: int | None = Field(default=1, description="Minimum degree of relationship (1 = parent/sibling/child)")
    degree_max: int | None = Field(default=None, description="Maximum degree (2 = also grandparents, aunts/uncles, half-siblings)")
    roles: list[Role] | None = Field(default=None, description="Restrict to these relationship roles, e.g. ['mother'] or ['sibling']")
    lineage: Literal["maternal", "paternal"] | None = None
    sex: Sex | None = None
    min_count: int = Field(default=1, ge=1, description="At least this many distinct relatives must match")
    where: "Criterion | None" = Field(default=None, description="Criteria the relative must satisfy; null = any relative")


Criterion = Annotated[
    Union[
        SexCriterion, BirthYearCriterion, AgeCriterion, PhenotypeCriterion, GeneCriterion,
        DiagnosisCriterion, HasRecordCriterion, RelativeCriterion, NotCriterion, GroupCriterion,
    ],
    Field(discriminator="kind"),
]


class Query(Strict):
    criteria: list[Criterion] = Field(description="Inclusion criteria; all must hold")
    exclusions: list[Criterion] = Field(
        default_factory=list,
        description="Exclusion criteria; a patient qualifies only if none of these hold (state them positively, e.g. 'diagnosis of Li-Fraumeni')",
    )
    reference_date: str | None = Field(default=None, description="ISO date used for age computation")


NotCriterion.model_rebuild()
GroupCriterion.model_rebuild()
RelativeCriterion.model_rebuild()
Query.model_rebuild()
