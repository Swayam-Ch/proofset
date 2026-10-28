"""A printable worksheet (HTML, A4) in English or German, with the answer key on its own page.

Many classrooms have a printer but not a device per student, so the practice set has to work on
paper. Open the file in a browser and print it.
"""

from __future__ import annotations

import html

from .schema import PracticeSet

LABELS = {
    "en": {"name": "Name", "date": "Date", "goals": "In this practice you will", "work": "Show your work",
           "key": "Answer key", "hints": "Hints", "checked": "Every answer was worked out twice by running code "
           "and matched the key before this sheet was printed.", "levels": {"warm-up": "Warm-up",
           "practice": "Practice", "challenge": "Challenge"}},
    "de": {"name": "Name", "date": "Datum", "goals": "In dieser Übung", "work": "Rechenweg",
           "key": "Lösungen", "hints": "Tipps", "checked": "Jede Lösung wurde zweimal mit Programmcode "
           "nachgerechnet und stimmte mit dem Lösungsschlüssel überein.", "levels": {"warm-up": "Aufwärmen",
           "practice": "Üben", "challenge": "Herausforderung"}},
}


def number(x: float, lang: str) -> str:
    from .verify import decimals

    text = f"{x:,.{min(decimals(x), 4)}f}"
    return text.replace(",", " ").replace(".", ",").replace(" ", ".") if lang == "de" else text


def answer(x: float, unit: str, lang: str) -> str:
    n = number(x, lang)
    if unit == "$":
        return f"{n} $" if lang == "de" else f"${n}"
    if unit == "%":
        return f"{n} %" if lang == "de" else f"{n}%"
    return f"{n} {unit}".strip()


def render(ps: PracticeSet, lang: str = "en") -> str:
    L = LABELS[lang]
    items = [i for i in ps.problems if i.check.status == "verified" and (lang == "en" or i.german)]
    e = html.escape
    rows, keys, n = [], [], 0
    for level in ("warm-up", "practice", "challenge"):
        group = [i for i in items if i.problem.level == level]
        if not group:
            continue
        rows.append(f"<h2>{L['levels'][level]}</h2>")
        for i in group:
            n += 1
            text = i.german if lang == "de" else i.problem
            rows.append(f'<div class="q"><p><b>{n}.</b> {e(text.question)}</p><div class="work">{L["work"]}</div></div>')
            hints = "".join(f"<li>{e(h)}</li>" for h in text.hints)
            keys.append(f"<li><b>{e(answer(i.problem.answer, i.problem.unit, lang))}</b><br>"
                        f"<span>{e(text.solution)}</span><details><summary>{L['hints']}</summary><ol>{hints}</ol></details></li>")
    goals = "".join(f"<li>{e(o)}</li>" for o in ps.objectives) if lang == "en" else ""
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<title>{e(ps.title)}</title><style>
@page {{ size: A4; margin: 18mm; }}
body {{ font: 12pt/1.45 Georgia, serif; color: #111; max-width: 760px; margin: 24px auto; padding: 0 16px; }}
h1 {{ font-size: 20pt; margin: 0 0 4px; }} h2 {{ font-size: 13pt; margin: 22px 0 6px; border-bottom: 1px solid #999; }}
.meta {{ display: flex; gap: 40px; margin: 10px 0 14px; }} .meta span {{ border-bottom: 1px solid #000; min-width: 180px; }}
.q {{ break-inside: avoid; margin: 10px 0 14px; }} .work {{ height: 90px; border: 1px dashed #bbb; color: #999;
font-size: 9pt; padding: 4px 6px; }} .key {{ break-before: page; }} .key li {{ margin-bottom: 10px; }}
.key span {{ color: #333; font-size: 11pt; }} .note, .src {{ font-size: 9pt; color: #555; }}
details {{ font-size: 10pt; }} @media print {{ details {{ display: none; }} }}
</style></head><body>
<h1>{e(ps.title)}</h1><div class="meta"><div>{L['name']}: <span></span></div><div>{L['date']}: <span></span></div></div>
{f"<p>{L['goals']}:</p><ul>{goals}</ul>" if goals else ""}
{''.join(rows)}
<section class="key"><h2>{L['key']}</h2><ol>{''.join(keys)}</ol><p class="note">{L['checked']}</p>
<p class="src">{e(ps.source)}</p></section></body></html>"""
