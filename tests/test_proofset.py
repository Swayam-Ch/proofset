import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "lambda" / "sandbox")]

from handler import handler  # noqa: E402

from proofset import agents, openstax, verify  # noqa: E402
from proofset.build import Builder  # noqa: E402
from proofset.sandbox import fix_escaped_newlines, format_result, make_run_python  # noqa: E402
from proofset.schema import Check, Draft, PracticeSet, Problem, SetProblem, Translation, TranslatedSet  # noqa: E402
from proofset.tutor import Session, student_number  # noqa: E402
from proofset.worksheet import answer, number, render  # noqa: E402

# ----------------------------------------------------------------------------- textbook parsing

CNXML = """<document xmlns="http://cnx.rice.edu/cnxml" xmlns:m="http://www.w3.org/1998/Math/MathML">
<title>Test Percents</title>
<metadata xmlns:md="http://cnx.rice.edu/mdml"><md:content-id>m1</md:content-id>
<md:abstract><list><item>Find a percent of a number</item></list></md:abstract></metadata>
<content>
<section><title>Teaching</title><para>To find <m:math><m:mtext>25%</m:mtext></m:math> of a number, multiply by
<m:math><m:mfrac><m:mn>1</m:mn><m:mn>4</m:mn></m:mfrac></m:math> or by <m:math><m:mn>2</m:mn><m:mfrac><m:mn>2</m:mn><m:mn>3</m:mn></m:mfrac></m:math>.</para></section>
<section class="section-exercises"><title>Exercises</title>
<para>In the following exercises, find the sale price.</para>
<exercise id="e1"><problem><para>Perla bought a cellphone that was on sale for $50 off. The original price of the cellphone was $189.</para></problem>
<solution><para>$139</para></solution></exercise>
<exercise id="e2"><problem><para>Use the table below to find the tax paid by each person on the list.</para></problem>
<solution><para>$12</para></solution></exercise>
<exercise id="e3"><problem><para>What number is 55% of 60?</para></problem><solution><para>33</para></solution></exercise>
</section></content></document>"""


def test_module_parsing_keeps_teaching_text_math_and_instructions():
    m = openstax.parse_module(CNXML)
    assert m.title == "Test Percents" and m.objectives == ["Find a percent of a number"]
    assert "25% of a number, multiply by 1/4 or by 2 2/3" in m.text and "cellphone" not in m.text
    e1, e2, e3 = m.exercises
    assert e1.question.startswith("In the following exercises, find the sale price.") and e1.answer == 139
    assert not e2.self_contained                                # needs a table
    assert [e.id for e in m.word_problems()] == ["e1"]         # e3 is too short to be a word problem


@pytest.mark.parametrize("text,value", [("33", 33), ("$35", 35), ("38.4%", 38.4), ("2,407 mg", 2407),
                                        ("1/4", 0.25), ("11.1(repeating)%, or 11 1/9%", None)])
def test_parse_answer(text, value):
    assert openstax.parse_answer(text) == value


# ----------------------------------------------------------------------------- checks


def solve(value, printed=None, ran=True):
    return verify.Solve(value, value if printed is None else printed, ran)


def test_check_verifies_a_key_two_code_runs_agree_with():
    assert verify.check(33, "", [solve(33), solve(33.0)])[0] == "verified"
    assert verify.check(21.2, "%", [solve(0.212), solve(21.2)])[0] == "verified"     # percent either way
    assert verify.check(38.5, "%", [solve(38.46), solve(38.46)])[0] == "verified"    # key's rounding


def test_check_catches_a_wrong_key_and_holds_back_doubtful_ones():
    status, reason, value = verify.check(50, "", [solve(20), solve(20)])
    assert status == "key wrong" and value == 20 and "key says 50" in reason
    assert verify.check(20, "", [solve(20), solve(21)])[0] == "unsure"                # solvers disagree
    assert verify.check(20, "", [solve(20, printed=25), solve(20)])[0] == "unsure"   # answer not from its code
    assert verify.check(20, "", [solve(20, ran=False), solve(20)])[0] == "unsure"    # no code ran


def test_german_numbers_must_match_the_english():
    ok, _ = verify.same_numbers("A $1,234.50 phone with 8.5% tax", "Ein Handy für 1.234,50 $ mit 8,5 % Steuer")
    assert ok
    ok, why = verify.same_numbers("It costs $1.65 in 2008.", "Es kostet 1,56 $ im Jahr 2008.")
    assert not ok and "1.65" in why


# ----------------------------------------------------------------------------- the sandbox tool


def test_sandbox_hides_credentials_and_stops_runaway_code(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "do-not-leak")
    assert "do-not-leak" not in handler({"code": "import os; print(os.environ.get('AWS_SECRET_ACCESS_KEY'))"})["stdout"]
    assert handler({"code": "while True: pass", "timeout": 1})["timed_out"]


def test_run_python_repairs_escaped_newlines_and_logs_runs():
    log = []
    out = make_run_python(handler, log=log)(code="x = 6\\nprint(x * 7)")
    assert "42" in out and log[0]["code"] == "x = 6\nprint(x * 7)"
    assert fix_escaped_newlines('print("a\\nb")') == 'print("a\\nb")'                 # valid as sent


def test_run_budget_cancels_calls_past_the_limit():
    budget, events = agents.RunBudget(limit=2), [SimpleNamespace(cancel_tool=False) for _ in range(3)]
    for e in events:
        budget.check(e)
    assert [bool(e.cancel_tool) for e in events] == [False, False, True]


# ----------------------------------------------------------------------------- the pipeline, scripted

PROGRAMS = {"What is 55% of 60?": "print(0.55 * 60)", "What is 10% of 200?": "print(0.10 * 200)",
            "Find 15% of 80.": "print(0.15 * 80)", "Which is bigger?": "print(1)"}


def problem(pid, question, key, level="practice"):
    return Problem(id=pid, level=level, objective="percent of a number", question=question, answer=key,
                   hints=["Write the percent as a decimal.", "Multiply."], solution=f"The answer is {key:g}.")


class Scripted:
    """Stands in for Bedrock: the author, two solvers per problem and the translator."""

    def __init__(self):
        self.translations = 0

    def __call__(self, model_id, system, prompt, code=False, output=None):
        if output is Draft and "did not match" in prompt:             # revision: fix the wrong key
            return agents.Reply(model_id, "", Draft(problems=[problem("p2", "What is 10% of 200?", 20)]))
        if output is Draft:
            return agents.Reply(model_id, "", Draft(problems=[
                problem("p1", "What is 55% of 60?", 33, "warm-up"), problem("p2", "What is 10% of 200?", 50),
                problem("p3", "Find 15% of 80.", 12, "challenge")]), 2000, 900)
        if output is TranslatedSet:
            self.translations += 1
            wrong_first = self.translations == 1
            return agents.Reply(model_id, "", TranslatedSet(title="Prozente testen", problems=[
                Translation(id="p1", question="Wie viel sind 55 % von 60?", hints=["Schreibe den Prozentsatz als "
                            "Dezimalzahl.", "Multipliziere."], solution="Die Lösung ist 33."),
                Translation(id="p2", question="Wie viel sind 10 % von 200?", hints=["Dezimalzahl.", "Multipliziere."],
                            solution="Die Lösung ist 20."),
                Translation(id="p3", question=f"Berechne {'16' if wrong_first else '15'} % von 80.",
                            hints=["Dezimalzahl.", "Multipliziere."], solution="Die Lösung ist 12.")]))
        code_text = PROGRAMS[prompt]
        result = handler({"code": code_text})
        return agents.Reply(model_id, f"ANSWER: {result['stdout'].strip()}", None, 300, 60,
                            runs=[{"code": code_text, "result": result}])


def build():
    return Builder(ask=Scripted()).build(openstax.parse_module(CNXML))


def test_build_ships_verified_keys_and_fixes_the_wrong_one():
    ps = build()
    status = {i.problem.id: (i.check.status, i.check.rounds, i.problem.answer) for i in ps.problems}
    assert status == {"p1": ("verified", 1, 33), "p2": ("verified", 2, 20), "p3": ("verified", 1, 12)}
    assert ps.stats["keys fixed after a code check"] == 1 and ps.cost_usd > 0


def test_a_translation_that_changes_a_number_is_retried():
    ps = build()
    p3 = next(i for i in ps.problems if i.problem.id == "p3")
    assert p3.german.question == "Berechne 15 % von 80." and ps.stats["translated"] == 3


def test_worksheets_render_in_both_languages():
    ps = build()
    en, de = render(ps, "en"), render(ps, "de")
    assert "What is 55% of 60?" in en and "Answer key" in en and "OpenStax" in en
    assert "Wie viel sind 55 % von 60?" in de and "Lösungen" in de and "Aufwärmen" in de
    assert "<h1>Prozente testen</h1>" in de and "Quelle: Prealgebra 2e" in de
    assert number(1234.5, "de") == "1.234,5" and answer(21.2, "%", "de") == "21,2 %" and answer(56, "$", "en") == "$56"
    assert answer(14.4, "$", "en") == "$14.40" and answer(1234.5, "$", "de") == "1.234,50 $"


# ----------------------------------------------------------------------------- the tutor's tools


@pytest.mark.parametrize("text,value", [("33", 33), ("It's $1,234.50", 1234.5), ("1.234,5", 1234.5),
                                        ("21,2 %", 21.2), ("21.2", 21.2), ("no idea", None)])
def test_student_numbers_in_either_style(text, value):
    assert student_number(text) == value


def test_tutor_session_checks_answers_gives_hints_and_logs():
    ps = PracticeSet(title="Percents", source="", objectives=[], problems=[
        SetProblem(problem=problem("p1", "What is 55% of 60?", 33), check=Check(status="verified", reason="")),
        SetProblem(problem=problem("p2", "Bad one", 1), check=Check(status="rejected", reason=""))])
    s = Session(ps, "en")
    shown = s.next_problem()
    assert "SHOW: Problem 1 of 1: What is 55% of 60?" in shown and len(s.items) == 1  # rejected never shown
    assert s.check_answer("p1", "30").startswith("NOT YET")
    assert s.get_hint("p1").startswith("Hint 1 of 2")
    assert s.check_answer("p1", "I think 33") == "CORRECT on attempt 2."
    assert "1 solved, 0 right on the first try, 1 hints used" in s.summary()


def test_two_small_solvers_alone_never_overrule_a_key():
    shared_mistake = [solve(10), solve(10)]
    assert verify.tiebreak(12, "", shared_mistake + [solve(12)], "key wrong", "", 10)[0] == "unsure"
    assert verify.tiebreak(12, "", shared_mistake + [solve(10)], "key wrong", "", 10)[0] == "key wrong"
    assert verify.tiebreak(12, "", shared_mistake + [solve(11)], "key wrong", "", 10)[0] == "unsure"
    assert verify.tiebreak(12, "", [solve(12), solve(13), solve(12)], "unsure", "", None)[0] == "verified"
