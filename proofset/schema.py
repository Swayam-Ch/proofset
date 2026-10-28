"""The shapes the agents exchange. Pydantic, so Strands can ask models for structured output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Level = Literal["warm-up", "practice", "challenge"]


class Problem(BaseModel):
    id: str = Field(description="Short id such as p1, p2")
    level: Level
    objective: str = Field(description="Which learning objective of the chapter this practises")
    question: str = Field(description="A self-contained word problem with one numeric answer")
    answer: float = Field(description="The numeric answer, with no units or % sign")
    unit: str = Field("", description="Unit of the answer, e.g. $, %, mg, years; empty if none")
    hints: list[str] = Field(description="2 or 3 hints, each one step further, never stating the answer")
    solution: str = Field(description="A short worked solution in 2 to 4 steps")


class Draft(BaseModel):
    problems: list[Problem]


class Translation(BaseModel):
    id: str
    question: str
    hints: list[str]
    solution: str
    unit: str = ""


class TranslatedSet(BaseModel):
    problems: list[Translation]


class Check(BaseModel):
    """What verification found for one problem."""

    status: Literal["verified", "rejected"]
    reason: str
    solver_values: list[float | None] = []
    rounds: int = 1  # 2 means the author's first answer key was wrong and was fixed


class SetProblem(BaseModel):
    problem: Problem
    check: Check
    german: Translation | None = None
    german_check: str = ""


class PracticeSet(BaseModel):
    title: str
    source: str
    objectives: list[str]
    problems: list[SetProblem]
    cost_usd: float = 0.0
    stats: dict[str, int] = {}
