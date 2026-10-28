"""Read OpenStax textbook modules (CNXML) straight from OpenStax's GitHub repositories.

No textbook text is stored in this repository. Modules are downloaded on first use and cached
under ~/.cache/proofset (or $PROOFSET_CACHE). Prealgebra 2e is licensed CC BY-NC-SA 4.0 by
OpenStax; anything generated from it keeps the attribution in `ATTRIBUTION`.
"""

from __future__ import annotations

import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

BOOKS = {
    "prealgebra-2e": {
        "repo": "openstax/osbooks-prealgebra-bundle",
        "title": "Prealgebra 2e",
        "url": "https://openstax.org/details/books/prealgebra-2e",
        "license": "CC BY-NC-SA 4.0",
    },
}
RAW = "https://raw.githubusercontent.com/{repo}/main/modules/{module}/index.cnxml"
CACHE = Path(os.environ.get("PROOFSET_CACHE", Path.home() / ".cache" / "proofset"))
C, M = "{http://cnx.rice.edu/cnxml}", "{http://www.w3.org/1998/Math/MathML}"


def attribution(book: str = "prealgebra-2e") -> str:
    b = BOOKS[book]
    return (f"Source: {b['title']} by OpenStax, {b['url']}, licensed {b['license']}. "
            "Practice problems are new questions written from this chapter.")


def fetch_module(module: str, book: str = "prealgebra-2e") -> str:
    """The module's CNXML, from the cache or from GitHub."""
    path = CACHE / book / f"{module}.cnxml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        url = RAW.format(repo=BOOKS[book]["repo"], module=module)
        with urllib.request.urlopen(url, timeout=30) as response:
            path.write_bytes(response.read())
    return path.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------- MathML and text


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def math_text(el: ET.Element) -> str:
    """A plain-text reading of a MathML element: 1/9, 2^10, 11.1(repeating), 55%."""
    tag, kids = _local(el.tag), list(el)
    if tag in ("mn", "mi", "mtext", "mo"):
        text = (el.text or "").strip()
        return {"−": "-", "−": "-", "⋅": "·"}.get(text, text)
    if tag == "mspace":
        return " "
    if tag == "mfrac" and len(kids) == 2:
        top, bottom = math_text(kids[0]), math_text(kids[1])
        wrap = lambda s: f"({s})" if re.search(r"[\s+\-·×]", s) else s  # noqa: E731
        return f"{wrap(top)}/{wrap(bottom)}"
    if tag == "msup" and len(kids) == 2:
        return f"{math_text(kids[0])}^{math_text(kids[1])}"
    if tag == "msqrt":
        return f"√({''.join(math_text(k) for k in kids)})"
    if tag in ("mover", "munder") and len(kids) == 2:
        base, mark = math_text(kids[0]), math_text(kids[1])
        return f"{base}(repeating)" if mark in ("¯", "‾", "_", "-", "―") else base
    out = ""
    for k in kids:
        piece = math_text(k)
        if out[-1:].isdigit() and _local(k.tag) == "mfrac":
            out += " "  # a mixed number: 2 2/3, not 22/3
        out += piece
    return out


def text_of(el: ET.Element, skip: tuple[str, ...] = ()) -> str:
    """The readable text of a CNXML element, with math inlined and whitespace collapsed."""
    parts: list[str] = []

    def walk(node: ET.Element) -> None:
        name = _local(node.tag)
        if name in skip:
            return
        if node.tag == f"{M}math":
            parts.append(" " + math_text(node) + " ")
        else:
            parts.append((node.text or "").replace("\n", " "))  # source line breaks are just spaces
            for child in node:
                walk(child)
                parts.append((child.tail or "").replace("\n", " "))
            if name in ("para", "item", "title", "row", "problem", "solution", "note"):
                parts.append("\n")

    walk(el)
    text = re.sub(r"[ \t]+", " ", "".join(parts))
    text = re.sub(r" ?\n ?", "\n", text)
    text = re.sub(r" ([,.?;:%)])", r"\1", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


# ----------------------------------------------------------------------------- answers

NUMBER = r"-?\d[\d,]*(?:\.\d+)?"


def parse_answer(text: str) -> float | None:
    """A single numeric answer such as '33', '$35', '38.4%', '1/9' or '0.75 mile'; else None."""
    s = text.strip().rstrip(".").replace("−", "-")
    m = re.fullmatch(rf"\$?\s*({NUMBER})\s*(%|percent|[a-zA-Z]+)?", s)
    if m:
        return float(m.group(1).replace(",", ""))
    m = re.fullmatch(rf"({NUMBER})\s*/\s*({NUMBER})", s)
    if m and float(m.group(2).replace(",", "")) != 0:
        return float(m.group(1).replace(",", "")) / float(m.group(2).replace(",", ""))
    return None


# ----------------------------------------------------------------------------- modules


@dataclass
class Exercise:
    id: str
    instruction: str  # the "In the following exercises, ..." line that applies to it, if any
    problem: str
    solution: str
    answer: float | None
    self_contained: bool  # no figure, table or graph needed to answer it

    @property
    def question(self) -> str:
        return f"{self.instruction}\n{self.problem}" if self.instruction else self.problem

    @property
    def word_problem(self) -> bool:
        return len(re.findall(r"[A-Za-z]{3,}", self.problem)) >= 8


@dataclass
class Module:
    id: str
    title: str
    objectives: list[str]
    text: str  # the teaching text and worked examples, without the end-of-section exercises
    exercises: list[Exercise] = field(default_factory=list)

    def numeric_exercises(self) -> list[Exercise]:
        """Exercises with a single official numeric answer that can be answered from the words alone."""
        return [e for e in self.exercises if e.answer is not None and e.self_contained]

    def word_problems(self) -> list[Exercise]:
        return [e for e in self.numeric_exercises() if e.word_problem]


def parse_module(xml: str) -> Module:
    root = ET.fromstring(xml)
    module_id = root.findtext(".//{http://cnx.rice.edu/mdml}content-id", default="")
    title = root.findtext(f"{C}title", default="").strip()
    abstract = root.find(".//{http://cnx.rice.edu/mdml}abstract")
    objectives = [text_of(i) for i in abstract.iter(f"{C}item")] if abstract is not None else []

    content = root.find(f"{C}content")
    teaching = []
    for section in (content if content is not None else []):
        if "exercises" in section.get("class", ""):
            continue
        teaching.append(text_of(section, skip=("media", "figure")))

    exercises, instruction = [], ""
    for ex in root.iter():
        if ex.tag == f"{C}para" and re.match(r"In the following exercises?,", text_of(ex)):
            instruction = text_of(ex)
            continue
        if ex.tag != f"{C}exercise":
            continue
        problem, solution = ex.find(f"{C}problem"), ex.find(f"{C}solution")
        if problem is None:
            continue
        sol_text = text_of(solution, skip=("title",)) if solution is not None else ""
        needs_more = any(problem.find(f".//{C}{t}") is not None for t in ("figure", "media", "table")) or re.search(
            r"\b(figure|table|graph|chart|shown|below|above|diagram)\b", text_of(problem), re.I)
        exercises.append(Exercise(ex.get("id", ""), instruction, text_of(problem), sol_text,
                                  parse_answer(sol_text), not needs_more))
    return Module(module_id, title, objectives, "\n\n".join(t for t in teaching if t), exercises)


def load(module: str, book: str = "prealgebra-2e") -> Module:
    return parse_module(fetch_module(module, book))


COLLECTION = "https://raw.githubusercontent.com/{repo}/main/collections/{book}.collection.xml"


def chapters(book: str = "prealgebra-2e") -> list[tuple[str, list[str]]]:
    """(chapter title, module ids) for every chapter of the book, from its collection file."""
    path = CACHE / book / "collection.xml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(COLLECTION.format(repo=BOOKS[book]["repo"], book=book), timeout=30) as r:
            path.write_bytes(r.read())
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    col, md = "{http://cnx.rice.edu/collxml}", "{http://cnx.rice.edu/mdml}"
    return [(sub.findtext(f"{md}title", "").strip(), [m.get("document") for m in sub.iter(f"{col}module")])
            for sub in root.iter(f"{col}subcollection")]
