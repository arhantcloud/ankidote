#!/usr/bin/env python3
"""Scrape GMAT practice questions from the Wizako question bank.

The Wizako question bank (https://practice-questions.wizako.com/gmat/) groups
questions by topic ("section"). Each section page lists questions inline, where
every question is an <li> inside an <ol class="ques..."> and contains:

  * one or more <p> tags with the question text,
  * an <ol class="choice..."> with the answer options, and
  * a "Correct Answer" tooltip whose <b> holds "Choice X".

This tool discovers the sections from the index page, scrapes up to N questions
from each, and writes them to JSON in the shape:

  {
    "section": ...,
    "question_name": ...,
    "difficulty": ...,
    "question_text": ...,
    "answer1": ..., "answer2": ..., ...,
    "correct_answer": "<letter>: <choice text>"
  }

Usage:
  python scrape_gmat.py                       # 20 questions/section -> gmat_questions.json
  python scrape_gmat.py --per-section 10
  python scrape_gmat.py --sections algebra critical-reasoning
  python scrape_gmat.py --output out.json --delay 1.0
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://practice-questions.wizako.com/gmat/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DIFFICULTY_RE = re.compile(r"(\d{3})\s*(?:to\s*\d{3}\s*)?level", re.IGNORECASE)
LETTERS = "ABCDEFGH"


def fetch(session: requests.Session, url: str) -> str | None:
    """GET a URL, returning decoded text or None on failure."""
    try:
        resp = session.get(url, timeout=30)
    except requests.RequestException as exc:  # network error
        print(f"  ! request failed for {url}: {exc}", file=sys.stderr)
        return None
    if resp.status_code != 200:
        print(f"  ! {resp.status_code} for {url}", file=sys.stderr)
        return None
    # The server omits a charset, so requests defaults to ISO-8859-1 and mangles
    # UTF-8 bytes. Prefer the encoding sniffed from the content.
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def discover_sections(index_html: str) -> list[dict]:
    """Find topic cards that link to on-site question pages.

    Returns a list of {"name", "slug", "url"} dicts, de-duplicated by URL.
    """
    soup = BeautifulSoup(index_html, "lxml")
    sections: list[dict] = []
    seen: set[str] = set()
    for anchor in soup.select("h4 a[href]"):
        href = anchor.get("href", "").strip()
        # Only same-site question pages live under quant/ or verbal/.
        if not (href.startswith("quant/") or href.startswith("verbal/")):
            continue
        url = urljoin(BASE_URL, href)
        if url in seen:
            continue
        seen.add(url)
        name = " ".join(anchor.get_text(" ", strip=True).split())
        # slug: first path segment after quant/ or verbal/
        parts = [p for p in href.split("/") if p]
        slug = parts[1] if len(parts) > 1 else parts[0]
        sections.append({"name": name, "slug": slug, "url": url})
    return sections


def normalize_math(soup) -> None:
    """Render superscripts/subscripts inline so exponents survive as text.

    GMAT quant relies heavily on exponents (e.g. 11^2), which would otherwise be
    flattened to "11 2" when the tags are stripped. Mutates the soup in place.
    """
    for sup in soup.find_all("sup"):
        sup.replace_with(f"^{sup.get_text(strip=True)}")
    for sub in soup.find_all("sub"):
        sub.replace_with(f"_{sub.get_text(strip=True)}")


def clean_text(node) -> str:
    """Collapse whitespace in the text content of a BeautifulSoup node."""
    text = node.get_text(" ", strip=True)
    # Tidy the spacing we introduce around ^ / _ math markers.
    text = re.sub(r"\s*\^\s*", "^", text)
    text = re.sub(r"\s*_\s*", "_", text)
    return " ".join(text.split())


def parse_correct_answer(question_li, choices: list[str]) -> tuple[str | None, str]:
    """Return (letter, human-readable correct answer) for a question <li>."""
    tip = question_li.select_one(".tooltiptext")
    if not tip:
        return None, ""
    bold = tip.find("b")
    label = clean_text(bold) if bold else ""
    # Any explanatory value after the <b>Choice X</b> (e.g. "9 Years old").
    extra = clean_text(tip)
    if label and extra.startswith(label):
        extra = extra[len(label):].strip()

    letter = None
    m = re.search(r"choice\s*([A-H])", label, re.IGNORECASE)
    if m:
        letter = m.group(1).upper()

    # Prefer the actual choice text; fall back to the tooltip's extra value.
    answer_text = ""
    if letter is not None:
        idx = LETTERS.index(letter)
        if idx < len(choices):
            answer_text = choices[idx]
    if not answer_text:
        answer_text = extra

    if letter and answer_text:
        return letter, f"{letter}: {answer_text}"
    if letter:
        return letter, letter
    return None, extra


def extract_difficulty(question_li) -> str | None:
    """Best-effort difficulty ("600 level" -> "600") from hint/explanation text."""
    m = DIFFICULTY_RE.search(question_li.get_text(" ", strip=True))
    return m.group(1) if m else None


def parse_questions(html: str, section_name: str, limit: int) -> list[dict]:
    """Extract up to `limit` questions from a section page."""
    soup = BeautifulSoup(html, "lxml")
    normalize_math(soup)
    records: list[dict] = []
    for choice_ol in soup.select("ol.choice"):
        question_li = choice_ol.find_parent("li")
        if question_li is None:
            continue

        # Question text = direct <p> children of the question <li>.
        paragraphs = [
            clean_text(p) for p in question_li.find_all("p", recursive=False)
        ]
        question_text = "\n".join(p for p in paragraphs if p)

        choices = [
            clean_text(li) for li in choice_ol.find_all("li", recursive=False)
        ]
        choices = [c for c in choices if c]
        if not question_text or len(choices) < 2:
            continue

        letter, correct_answer = parse_correct_answer(question_li, choices)
        difficulty = extract_difficulty(question_li)

        record = {
            "section": section_name,
            "question_name": f"{section_name} Q{len(records) + 1}",
            "difficulty": difficulty,
            "question_text": question_text,
        }
        for i, choice in enumerate(choices, start=1):
            record[f"answer{i}"] = choice
        record["correct_answer"] = correct_answer or None
        records.append(record)

        if len(records) >= limit:
            break
    return records


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape GMAT practice questions from the Wizako question bank."
    )
    parser.add_argument(
        "--per-section", type=int, default=20,
        help="Max questions to scrape per section (default: 20).",
    )
    parser.add_argument(
        "--output", default="gmat_questions.json",
        help="Output JSON file path (default: gmat_questions.json).",
    )
    parser.add_argument(
        "--sections", nargs="*", default=None,
        help="Optional list of section slugs to include (default: all discovered).",
    )
    parser.add_argument(
        "--delay", type=float, default=0.75,
        help="Seconds to wait between page requests (default: 0.75).",
    )
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    print(f"Fetching index: {BASE_URL}")
    index_html = fetch(session, BASE_URL)
    if index_html is None:
        print("Could not fetch the index page; aborting.", file=sys.stderr)
        return 1

    sections = discover_sections(index_html)
    if args.sections:
        wanted = set(args.sections)
        sections = [s for s in sections if s["slug"] in wanted]
    print(f"Discovered {len(sections)} section(s): "
          + ", ".join(s["slug"] for s in sections))

    all_records: list[dict] = []
    summary: list[tuple[str, int]] = []
    for section in sections:
        print(f"\n== {section['name']} ({section['slug']}) ==")
        print(f"   {section['url']}")
        time.sleep(args.delay)
        html = fetch(session, section["url"])
        if html is None:
            summary.append((section["name"], 0))
            continue
        records = parse_questions(html, section["name"], args.per_section)
        print(f"   -> {len(records)} question(s)")
        all_records.extend(records)
        summary.append((section["name"], len(records)))

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(all_records, fh, ensure_ascii=False, indent=2)

    print("\n===== Summary =====")
    for name, count in summary:
        print(f"  {name:32s} {count:3d}")
    print(f"  {'TOTAL':32s} {len(all_records):3d}")
    print(f"\nWrote {len(all_records)} questions to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
