"""Build a verified practice set from one textbook section.

    1. author     drafts problems with answer keys from the chapter text
    2. solvers    two cheap solvers work each problem out with code, in parallel
    3. check      a key ships only if two code runs agree with it (verify.py)
                  disagreement -> one more solve on the larger model
                  wrong key    -> back to the author once, then re-checked
    4. translate  verified problems into German; every number must survive (verify.same_numbers)
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from . import agents, openstax, verify
from .schema import Check, Draft, PracticeSet, Problem, SetProblem, TranslatedSet

Ask = Callable[..., agents.Reply]
LEVELS = {"warm-up": 3, "practice": 4, "challenge": 3}


class Builder:
    def __init__(self, ask: Ask = agents.bedrock_ask, workers: int = 8):
        self.ask, self.workers, self.replies = ask, workers, []

    def _ask(self, *args, **kwargs) -> agents.Reply:
        reply = self.ask(*args, **kwargs)
        self.replies.append(reply)
        return reply

    # ------------------------------------------------------------------ solving
    def solve(self, problem: Problem, model: str) -> verify.Solve:
        reply = self._ask(model, agents.SOLVER, problem.question, code=True)
        return verify.Solve(verify.answer_line(reply.text), verify.printed_value(reply.runs), bool(reply.runs))

    def check(self, problem: Problem, rounds: int = 1) -> tuple[str, Check, float | None]:
        with ThreadPoolExecutor(2) as pool:
            solves = list(pool.map(lambda _: self.solve(problem, agents.SMALL), range(2)))
        status, reason, value = verify.check(problem.answer, problem.unit, solves)
        if status == "unsure":
            solves.append(self.solve(problem, agents.LARGE))
            status, reason, value = verify.check(problem.answer, problem.unit, solves)
        result = Check(status="verified" if status == "verified" else "rejected", reason=reason,
                       solver_values=[s.answer for s in solves], rounds=rounds)
        return status, result, value

    # ------------------------------------------------------------------ the pipeline
    def build(self, module: openstax.Module, levels: dict[str, int] = LEVELS, german: bool = True) -> PracticeSet:
        draft = self._ask(agents.LARGE, agents.AUTHOR,
                          agents.author_prompt(module.title, module.objectives, module.text, levels), output=Draft)
        problems: list[Problem] = draft.structured.problems

        with ThreadPoolExecutor(self.workers) as pool:
            results = list(pool.map(self.check, problems))
        checked = {p.id: (p, r) for p, r in zip(problems, results)}

        wrong = [(p, value) for p, (status, _, value) in zip(problems, results) if status == "key wrong"]
        if wrong:  # one revision round: the author sees what the code found
            prompt = agents.revise_prompt([{**p.model_dump(), "found": v} for p, v in wrong])
            revised = self._ask(agents.LARGE, agents.AUTHOR, prompt, output=Draft).structured.problems
            revised = [p for p in revised if p.id in checked]
            with ThreadPoolExecutor(self.workers) as pool:
                again = list(pool.map(lambda p: self.check(p, rounds=2), revised))
            for p, r in zip(revised, again):
                checked[p.id] = (p, r)

        items = [SetProblem(problem=p, check=r[1]) for p, r in checked.values()]
        title_de = self.translate([i for i in items if i.check.status == "verified"], module.title) if german else ""

        verified = [i for i in items if i.check.status == "verified"]
        return PracticeSet(
            title=module.title, title_de=title_de, source=openstax.attribution(), objectives=module.objectives, problems=items,
            cost_usd=sum(r.cost_usd for r in self.replies),
            stats={"drafted": len(problems), "verified": len(verified),
                   "keys fixed after a code check": sum(i.check.rounds == 2 for i in verified),
                   "rejected": len(items) - len(verified),
                   "translated": sum(i.german is not None for i in verified)})

    def translate(self, items: list[SetProblem], title: str, tries: int = 2) -> str:
        """Translate the verified problems; returns the German chapter title."""
        todo, title_de = items, ""
        for attempt in range(tries):
            if not todo:
                break
            payload = f"Chapter title: {title}\n\n" + "\n\n".join(
                f"id: {i.problem.id}\nquestion: {i.problem.question}\nunit: {i.problem.unit}\n"
                f"hints: {i.problem.hints}\nsolution: {i.problem.solution}" for i in todo)
            if attempt:
                payload = "Last time some numbers changed. Keep every number exactly.\n\n" + payload
            out = self._ask(agents.LARGE, agents.TRANSLATOR, payload, output=TranslatedSet).structured
            title_de = title_de or out.title
            by_id = {t.id: t for t in out.problems}
            retry = []
            for item in todo:
                t = by_id.get(item.problem.id)
                ok, why = (False, "missing") if t is None else verify.same_numbers(
                    " ".join([item.problem.question, item.problem.solution, *item.problem.hints]),
                    " ".join([t.question, t.solution, *t.hints]))
                if ok:
                    item.german, item.german_check = t, "same numbers as the English"
                else:
                    item.german_check, retry = f"not used: {why}", retry + [item]
            todo = retry
        return title_de
