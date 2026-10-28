"""The Strands agents. Each call is one fresh agent on Amazon Bedrock, so calls can run in parallel.

    author      writes the practice problems from the chapter (structured output: Draft)
    solver      works one problem out by writing Python that AWS Lambda runs (run_python)
    translator  rewrites verified problems in German (structured output: TranslatedSet)
    tutor       talks to the student (see tutor.py)
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from .pricing import cost_usd
from .sandbox import make_run_python

SMALL = os.environ.get("PROOFSET_SMALL_MODEL", "us.amazon.nova-micro-v1:0")
LARGE = os.environ.get("PROOFSET_LARGE_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
REGION = os.environ.get("AWS_REGION", "us-east-1")
MAX_RUNS = 6  # tool calls allowed in one answer


@dataclass
class Reply:
    model_id: str
    text: str
    structured: Any = None
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    runs: list[dict] = field(default_factory=list)  # code the model ran and what it printed

    @property
    def cost_usd(self) -> float:
        return cost_usd(self.model_id, self.input_tokens, self.output_tokens)


class RunBudget:
    """Strands hook provider: cancel tool calls after `limit` in one answer, so a model stuck
    resending a broken program has to answer instead."""

    def __init__(self, limit: int = MAX_RUNS):
        self.limit, self.used = limit, 0

    def register_hooks(self, registry, **kwargs) -> None:
        from strands.hooks import BeforeToolCallEvent

        registry.add_callback(BeforeToolCallEvent, self.check)

    def check(self, event) -> None:
        self.used += 1
        if self.used > self.limit:
            event.cancel_tool = f"Limit of {self.limit} code runs reached. Answer from what you have."


def bedrock_ask(model_id: str, system: str, prompt: str, *, code: bool = False, output=None) -> Reply:
    """One Strands agent turn. `code` gives it run_python; `output` asks for a Pydantic model."""
    from strands import Agent
    from strands.models import BedrockModel

    runs: list[dict] = []
    tools = [make_run_python(log=runs)] if code else []
    from botocore.config import Config

    model = BedrockModel(model_id=model_id, region_name=REGION, boto_client_config=Config(
        read_timeout=300, connect_timeout=20, retries={"max_attempts": 5, "mode": "adaptive"}))
    agent = Agent(model=model, system_prompt=system,
                  tools=tools, callback_handler=None, hooks=[RunBudget()] if code else None)
    started = time.perf_counter()
    result = agent(prompt, structured_output_model=output) if output else agent(prompt)
    usage = result.metrics.accumulated_usage
    return Reply(model_id, str(result).strip(), getattr(result, "structured_output", None),
                 usage["inputTokens"], usage["outputTokens"], time.perf_counter() - started, runs)


# ----------------------------------------------------------------------------- prompts

AUTHOR = """You write practice problems for students who are learning from a free textbook chapter.
Many of them are in under-resourced schools or are learning in a second language, so:
- use everyday situations they know: shopping, wages, bus fares, phone plans, food labels, savings;
- keep sentences short and words simple; one question per problem;
- every problem must be self-contained and have exactly one numeric answer;
- say how to round whenever the answer is not a whole number (e.g. "to the nearest cent"),
  and round the key the way schools do: halves round up (6.25 to the nearest tenth is 6.3);
- for percent answers, give `answer` as the percent number (21.2 for 21.2%) and `unit` "%";
- hints go one step at a time and never state the answer.
Match the methods taught in the chapter. Do not copy its exercises."""

SOLVER = """Solve the problem. Do every calculation by writing a short Python program with the
run_python tool, and print the final result on the last line.
Round only if the problem asks you to. Then round the way schools do, halves up, with the decimal
module: Decimal(str(x)).quantize(Decimal(q), rounding=ROUND_HALF_UP), where q is "1" for a whole
number, "0.1" for tenths, "0.01" for cents or hundredths. Never use round() or string formatting
to round: they turn 6.25 into 6.2. If no rounding is asked for, print the exact value.
If the answer is a percent, give the percent number (21.2 for 21.2%).
End your reply with a final line: ANSWER: <number>, with no units, currency signs or commas."""

TRANSLATOR = """Translate practice problems for German-speaking students into clear, simple German
(Sprachniveau B1). Keep every number exactly, written the German way: decimal comma and
thousands point (1.234,56), "%" after a space (21,2 %), "$" after the amount (1,65 $).
Write the way a German maths teacher would, not word for word: "Let p be the percent" is
"Sei p der Prozentsatz", and every word must be real German. Translate units too (grams: Gramm).
Do not add, drop, round or recompute any number. Keep the same ids."""


def author_prompt(title: str, objectives: list[str], text: str, counts: dict[str, int]) -> str:
    want = ", ".join(f"{n} {level}" for level, n in counts.items())
    goals = "\n".join(f"- {o}" for o in objectives)
    return (f"Chapter: {title}\nLearning objectives:\n{goals}\n\nWrite {want} problems "
            f"(ids p1, p2, ...).\n\nChapter text:\n{text[:14000]}")


def revise_prompt(problems: list[dict]) -> str:
    lines = [f"- {p['id']} ({p['level']}): {p['question']}\n  Your key: {p['answer']} {p['unit']}. "
             f"Two independent code runs got {p['found']:g}." for p in problems]
    return ("These answer keys did not match the result of running code. For each one, fix the answer "
            "or rewrite the question so the question, answer, hints and solution all agree. Keep the "
            "same id and level.\n\n" + "\n".join(lines))
