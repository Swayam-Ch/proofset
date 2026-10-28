"""proofset: verified practice sets from free textbooks, in English and German.

    proofset sections Percents             list the sections of a chapter
    proofset build m81313                  write sets/m81313.json and printable worksheets (en, de)
    proofset tutor sets/m81313.json --lang de
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import openstax


def cmd_sections(args) -> int:
    for title, modules in openstax.chapters(args.book):
        if args.chapter.lower() in title.lower():
            print(title)
            for mid in modules:
                m = openstax.load(mid, args.book)
                print(f"  {mid}  {m.title}  ({len(m.word_problems())} word problems with official answers)")
    return 0


def cmd_build(args) -> int:
    from .build import Builder
    from .worksheet import render

    module = openstax.load(args.module, args.book)
    started = time.perf_counter()
    ps = Builder().build(module, german=not args.english_only)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.module}.json").write_text(ps.model_dump_json(indent=2))
    for lang in ("en",) if args.english_only else ("en", "de"):
        (out / f"{args.module}-{lang}.html").write_text(render(ps, lang), encoding="utf-8")
    print(f"{ps.title}: " + ", ".join(f"{k} {v}" for k, v in ps.stats.items()))
    for item in ps.problems:
        mark = "✓" if item.check.status == "verified" else "✗"
        extra = " (key fixed after a code check)" if item.check.rounds == 2 and mark == "✓" else ""
        print(f"  {mark} {item.problem.id} {item.problem.level:<9} {item.check.reason}{extra}")
    print(f"LLM cost ${ps.cost_usd:.4f}, {time.perf_counter() - started:.0f} s. Written to {out}/")
    return 0


def cmd_tutor(args) -> int:
    from .schema import PracticeSet
    from .tutor import Session, make_agent

    path = Path(args.practice_set)
    session = Session(PracticeSet.model_validate_json(path.read_text()), args.lang)
    agent = make_agent(session)
    greeting = {"en": "Hi! Give me my first problem, please.", "de": "Hallo! Gib mir bitte die erste Aufgabe."}
    print(f"\n{agent(greeting[args.lang])}\n")
    while True:
        try:
            said = input("you> ").strip()
        except EOFError:
            break
        if said.lower() in {"exit", "quit", "ende"}:
            break
        if said:
            print(f"\n{agent(said)}\n")
    log = path.with_name(f"{path.stem}-session-{time.strftime('%Y%m%d-%H%M%S')}.json")
    session.save(log)
    print(f"{session.summary()}\nSession log: {log}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--book", default="prealgebra-2e", choices=sorted(openstax.BOOKS))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("sections", help="list a chapter's sections")
    p.add_argument("chapter", nargs="?", default="Percents")
    p.set_defaults(run=cmd_sections)
    p = sub.add_parser("build", help="build a verified practice set from one section")
    p.add_argument("module", help="module id, e.g. m81313 (see `proofset sections`)")
    p.add_argument("--out", default="sets")
    p.add_argument("--english-only", action="store_true")
    p.set_defaults(run=cmd_build)
    p = sub.add_parser("tutor", help="practise a set with the tutor")
    p.add_argument("practice_set")
    p.add_argument("--lang", default="en", choices=["en", "de"])
    p.set_defaults(run=cmd_tutor)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
