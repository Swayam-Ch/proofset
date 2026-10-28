"""The student-facing tutor: a Strands agent whose tools hold the verified answers.

The model never decides whether a student is right. `check_answer` compares the student's number
with the code-verified key, and the model only explains, encourages and gives hints. Every attempt
is logged, so a teacher can see first-try accuracy, hints used and progress over a session.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .schema import PracticeSet
from .verify import matches_key
from .worksheet import answer as show_answer

PROMPT = {
    "en": "You are a patient maths tutor for a student practising {title}. Speak simple English.",
    "de": "Du bist eine geduldige Mathe-Nachhilfe für Schüler, die {title} üben. Sprich einfaches Deutsch "
          "(Sprachniveau B1) und duze die Schülerin oder den Schüler.",
}
RULES = """Use the tools for everything about the problems:
- next_problem to give the student a problem; show the student only the text after "SHOW:".
  The id is for check_answer and get_hint; never show it.
- check_answer whenever the student gives a number. Never decide yourself whether it is right.
- get_hint when the student is stuck or wrong; give one hint at a time.
Never state the answer before check_answer reveals it. Keep replies to 2 to 4 short sentences."""


def student_number(text: str) -> float | None:
    """The last number a student typed, in English (1,234.5) or German (1.234,5) style."""
    found = re.findall(r"-?\d[\d.,]*\d|-?\d", text)
    if not found:
        return None
    raw = found[-1]
    if re.fullmatch(r"-?\d{1,3}([.,]\d{3})+", raw):          # 1,234 or 1.234: thousands
        return float(re.sub(r"[.,]", "", raw))
    if "," in raw and "." in raw:                           # both: the last one is the decimal mark
        decimal = "," if raw.rfind(",") > raw.rfind(".") else "."
        raw = raw.replace("." if decimal == "," else ",", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")                         # 21,2 or 21.2
    try:
        return float(raw)
    except ValueError:
        return None


@dataclass
class Session:
    practice: PracticeSet
    lang: str = "en"
    current: int = -1
    attempts: dict[str, int] = field(default_factory=dict)
    hints: dict[str, int] = field(default_factory=dict)
    solved: dict[str, bool] = field(default_factory=dict)
    log: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.items = [i for i in self.practice.problems
                      if i.check.status == "verified" and (self.lang == "en" or i.german)]

    def text(self, item):
        return item.german if self.lang == "de" else item.problem

    # ---------------------------------------------------------------- tool bodies (plain Python)
    def next_problem(self) -> str:
        self.current += 1
        if self.current >= len(self.items):
            return "DONE: no problems left. " + self.summary()
        item = self.items[self.current]
        label = "Aufgabe {n} von {total}" if self.lang == "de" else "Problem {n} of {total}"
        return (f"id: {item.problem.id}\nSHOW: {label.format(n=self.current + 1, total=len(self.items))}: "
                f"{self.text(item).question}")

    def check_answer(self, problem_id: str, student_answer: str) -> str:
        item = next((i for i in self.items if i.problem.id == problem_id), None)
        if item is None:
            return f"Unknown problem id {problem_id}."
        value = student_number(student_answer)
        n = self.attempts[problem_id] = self.attempts.get(problem_id, 0) + 1
        right = matches_key(value, item.problem.answer, item.problem.unit)
        self.log.append({"t": time.time(), "id": problem_id, "attempt": n, "answer": student_answer,
                         "correct": right, "hints_used": self.hints.get(problem_id, 0)})
        if right:
            self.solved[problem_id] = True
            return f"CORRECT on attempt {n}."
        if n >= 3:
            self.solved.setdefault(problem_id, False)
            key = show_answer(item.problem.answer, item.problem.unit, self.lang)
            return f"NOT YET after 3 attempts. The answer is {key}. Worked solution: {self.text(item).solution}"
        return f"NOT YET (attempt {n} of 3). Do not reveal the answer; offer a hint."

    def get_hint(self, problem_id: str) -> str:
        item = next((i for i in self.items if i.problem.id == problem_id), None)
        if item is None:
            return f"Unknown problem id {problem_id}."
        hints = self.text(item).hints
        k = self.hints.get(problem_id, 0)
        if k >= len(hints):
            return "No more hints. Suggest the student tries once more."
        self.hints[problem_id] = k + 1
        return f"Hint {k + 1} of {len(hints)}: {hints[k]}"

    def summary(self) -> str:
        tried = [p for p in self.attempts]
        first_try = sum(1 for e in self.log if e["attempt"] == 1 and e["correct"])
        return (f"{len(tried)} problems tried, {sum(self.solved.values())} solved, {first_try} right on the "
                f"first try, {sum(self.hints.values())} hints used.")

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"set": self.practice.title, "lang": self.lang, "summary": self.summary(),
                                    "log": self.log}, ensure_ascii=False, indent=2))


def make_agent(session: Session, model_id: str | None = None):
    from strands import Agent, tool
    from strands.models import BedrockModel

    from .agents import REGION, SMALL

    @tool
    def next_problem() -> str:
        """Get the next practice problem for the student."""
        return session.next_problem()

    @tool
    def check_answer(problem_id: str, student_answer: str) -> str:
        """Check the student's answer against the verified answer key.

        Args:
            problem_id: The id of the problem, e.g. p3.
            student_answer: What the student said, including the number.
        """
        return session.check_answer(problem_id, student_answer)

    @tool
    def get_hint(problem_id: str) -> str:
        """Get the next hint for a problem.

        Args:
            problem_id: The id of the problem, e.g. p3.
        """
        return session.get_hint(problem_id)

    system = PROMPT[session.lang].format(title=session.practice.title) + "\n" + RULES
    return Agent(model=BedrockModel(model_id=model_id or SMALL, region_name=REGION), system_prompt=system,
                 tools=[next_problem, check_answer, get_hint], callback_handler=None)
