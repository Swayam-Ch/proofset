"""How often is an LLM-written answer key wrong, and does proofset's check stop the wrong ones?

Ground truth: OpenStax Prealgebra 2e word problems that come with an official numeric answer.
For each problem:

    key    the large model answers without tools, the way an author model writes a key from its head
    check  proofset's check of that key: two Nova Micro + run_python solves, plus one on the large
           model if they disagree; the key ships only if two code runs agree with it

Compared with the official answer, each problem ends up as one of:

    good key shipped    the key was right and the check passed it
    WRONG KEY SHIPPED   the key was wrong and the check passed it (the number we want at zero)
    wrong key caught    the key was wrong and the check stopped it
    good key dropped    the key was right but the check stopped it (costs a problem, not accuracy)

    python bench/key_bench.py               # all Percents sections
    python bench/key_bench.py --limit 10    # quick check
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from proofset import agents, openstax, verify  # noqa: E402
from proofset.build import Builder  # noqa: E402
from proofset.schema import Problem  # noqa: E402

PERCENTS = ["m81306", "m81313", "m81307", "m81308", "m81311"]
TITLES = {"m81306": "Understand Percent", "m81313": "General Applications of Percent",
          "m81307": "Sales Tax, Commission, Discount", "m81308": "Simple Interest",
          "m81311": "Proportions"}
KEY_WRITER = """Solve the problem. End your reply with a final line: ANSWER: <number>, with no units,
currency signs or commas. If the answer is a percent, give the percent number (21.2 for 21.2%)."""
OUTCOMES = ["good key shipped", "WRONG KEY SHIPPED", "wrong key caught", "good key dropped", "no key"]


def unit_of(solution: str) -> str:
    return "%" if "%" in solution else "$" if "$" in solution else ""


def outcome(key_right: bool, shipped: bool) -> str:
    if shipped:
        return "good key shipped" if key_right else "WRONG KEY SHIPPED"
    return "good key dropped" if key_right else "wrong key caught"


def summarise(rows: list[dict], cost: float) -> str:
    n = len(rows)
    counts = {o: sum(r["outcome"] == o for r in rows) for o in OUTCOMES}
    wrong_keys = sum(not r["key_right"] for r in rows if r["key"] is not None)
    shipped = counts["good key shipped"] + counts["WRONG KEY SHIPPED"]
    lines = ["# Answer keys: written by a model vs checked by proofset", "",
             f"{n} OpenStax Prealgebra 2e word problems with official answers.", "",
             f"- Keys written by {agents.LARGE.split('.')[-1]} without tools: {wrong_keys} of {n} wrong "
             f"({wrong_keys / max(n, 1):.0%})",
             f"- Keys shipped after proofset's check: {shipped}, of which wrong: {counts['WRONG KEY SHIPPED']}",
             f"- LLM cost for the whole run: ${cost:.4f}", "", "| Outcome | Problems |", "| --- | ---: |"]
    lines += [f"| {o} | {c} |" for o, c in counts.items()]
    lines += ["", "## By section", "", "| Section | Problems | Wrong keys written | Wrong keys shipped | "
              "Good keys dropped |", "| --- | ---: | ---: | ---: | ---: |"]
    for mid in dict.fromkeys(r["module"] for r in rows):
        mine = [r for r in rows if r["module"] == mid]
        lines.append(f"| {mid} {TITLES.get(mid, '')} | {len(mine)} | {sum(not r['key_right'] for r in mine)} | "
                     f"{sum(r['outcome'] == 'WRONG KEY SHIPPED' for r in mine)} | "
                     f"{sum(r['outcome'] == 'good key dropped' for r in mine)} |")
    lines += ["", "## Wrong keys", "", "| Problem | Official | Key | Check |", "| --- | ---: | ---: | --- |"]
    lines += [f"| `{r['module']}/{r['id']}` | {r['official']} | {r['key']} | {r['outcome']}: {r['reason']} |"
              for r in rows if not r["key_right"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modules", default=",".join(PERCENTS))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    exercises = [(mid, e) for mid in args.modules.split(",") for e in openstax.load(mid).word_problems()]
    exercises = exercises[: args.limit] if args.limit else exercises
    builder = Builder()

    def run(item):
        mid, ex = item
        unit = unit_of(ex.solution)
        written = builder._ask(agents.LARGE, KEY_WRITER, ex.question)
        key = verify.answer_line(written.text)
        row = {"module": mid, "id": ex.id, "question": ex.question, "official": ex.answer, "unit": unit,
               "key": key, "key_right": verify.matches_key(key, ex.answer, unit)}
        if key is None:
            return {**row, "outcome": "no key", "reason": "no ANSWER line", "solver_values": []}
        problem = Problem(id=ex.id, level="practice", objective="", question=ex.question, answer=key, unit=unit,
                          hints=[], solution="")
        status, check, _ = builder.check(problem)
        shipped = check.status == "verified"
        print(f"{mid}/{ex.id:<22} official {ex.answer:<10g} key {key:<10g} "
              f"{'ship' if shipped else 'stop'}  {check.reason}")
        return {**row, "outcome": outcome(row["key_right"], shipped), "reason": check.reason,
                "solver_values": check.solver_values}

    with ThreadPoolExecutor(args.workers) as pool:
        rows = list(pool.map(run, exercises))

    out = ROOT / "bench" / "results" / time.strftime("keys-%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    (out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    summary = summarise(rows, sum(r.cost_usd for r in builder.replies))
    (out / "summary.md").write_text(summary)
    print("\n" + summary + f"\nwritten to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
