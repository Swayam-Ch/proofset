"""Plain-Python checks: no model decides whether an answer key or a translation is right.

An answer key is accepted only when two independent solvers, each of which ran code, printed the
same value and that value matches the key. A German translation is accepted only when it contains
exactly the same numbers as the English problem (German writes 1.234,5 for 1,234.5).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


@dataclass
class Solve:
    """One solver's attempt: the number it answered and the number its last clean program printed."""

    answer: float | None
    printed: float | None
    ran_code: bool

    @property
    def grounded(self) -> bool:
        return self.ran_code and close(self.answer, self.printed)


def numbers(text: str) -> list[float]:
    return [float(n.replace(",", "")) for n in NUMBER.findall(text)]


def answer_line(text: str) -> float | None:
    found = re.findall(r"ANSWER:\s*\**\s*\$?\s*(-?[\d,]*\.?\d+)", text)
    return float(found[-1].replace(",", "")) if found else None


def printed_value(runs: list[dict]) -> float | None:
    """The last number on the last line printed by the last program that ran cleanly."""
    for run in reversed(runs):
        r = run["result"]
        lines = [line for line in (r.get("stdout") or "").splitlines() if line.strip()]
        if r.get("exit_code") == 0 and not r.get("timed_out") and lines:
            found = numbers(lines[-1])
            return found[-1] if found else None
    return None


def decimals(x: float) -> int:
    text = f"{x:.10f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def close(a: float | None, b: float | None, rel: float = 1e-6) -> bool:
    return a is not None and b is not None and abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def matches_key(value: float | None, key: float, unit: str = "") -> bool:
    """`value` equals the key at the key's precision; for percents, 0.212 also matches 21.2."""
    if value is None:
        return False
    tol = 0.5 * 10 ** -decimals(key) + 1e-9
    candidates = [value, value * 100] if unit.strip() == "%" else [value]
    return any(abs(c - key) <= tol or close(c, key) for c in candidates)


def check(key: float, unit: str, solves: list[Solve]) -> tuple[str, str, float | None]:
    """('verified' | 'key wrong' | 'unsure', reason, the value two solvers agreed on)."""
    grounded = [s for s in solves if s.grounded]
    for i, a in enumerate(grounded):
        for b in grounded[i + 1:]:
            if close(a.answer, b.answer, rel=1e-4) or (matches_key(a.answer, key, unit) and matches_key(b.answer, key, unit)):
                if matches_key(a.answer, key, unit):
                    return "verified", f"two code runs got {a.answer:g}, matching the key", a.answer
                return "key wrong", f"two code runs got {a.answer:g}, but the key says {key:g}", a.answer
    if len(grounded) < 2:
        return "unsure", f"only {len(grounded)} solver(s) answered from code they ran", None
    return "unsure", "the solvers disagree: " + ", ".join(f"{s.answer:g}" for s in grounded), None


# ----------------------------------------------------------------------------- German numbers

DE_NUMBER = re.compile(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+(?:,\d+)?")


def german_numbers(text: str) -> list[float]:
    return [float(n.replace(".", "").replace(",", ".")) for n in DE_NUMBER.findall(text)]


def same_numbers(english: str, german: str) -> tuple[bool, str]:
    en, de = Counter(numbers(english)), Counter(german_numbers(german))
    if en == de:
        return True, "same numbers"
    missing, extra = en - de, de - en
    return False, (f"missing {sorted(missing.elements())}" if missing else "") + \
        (f" extra {sorted(extra.elements())}" if extra else "")
