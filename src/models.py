from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Salary:
    salary_from: int | None = None
    salary_to: int | None = None
    currency: str | None = None


@dataclass(slots=True)
class Vacancy:
    source: str
    external_id: str
    title: str
    company: str
    url: str
    area: str | None = None
    schedule: str | None = None
    experience: str | None = None
    employment: str | None = None
    salary: Salary = field(default_factory=Salary)
    description: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ScoreResult:
    score: int
    status: str
    career_value: int
    reasons_positive: list[str]
    reasons_negative: list[str]
