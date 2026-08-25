"""Baseline template-matching agent for Meridian.

Matches a natural-language question against a catalog of regex patterns and
runs the hand-written SQL attached to the matching template. There is no
generation and no model — this is the floor that phase 6's LLM agent gets
measured against.

Design rule: REFUSE rather than guess. An agent that says "no template
matches" is more useful than one that fuzzy-matches to the nearest pattern
and returns a confidently wrong number. The refusal rate is itself the
metric — it is exactly the share of questions the LLM needs to earn.

Usage:
    python agent/baseline_agent.py "what is our total revenue"
    python agent/baseline_agent.py --list
    python agent/baseline_agent.py --benchmark
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from templates import TEMPLATES, Template  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "meridian.duckdb"


@dataclass
class AgentResult:
    question: str
    matched: bool
    template_name: str | None = None
    sql: str | None = None
    measure: str | None = None
    notes: str = ""
    rows: list | None = None
    columns: list | None = None
    error: str | None = None

    @property
    def refused(self) -> bool:
        return not self.matched


def normalise(question: str) -> str:
    """Lowercase, strip punctuation noise, collapse whitespace."""
    q = question.lower().strip()
    q = re.sub(r"[?!.,;]+$", "", q)
    q = re.sub(r"\s+", " ", q)
    return q


def match_template(question: str) -> Template | None:
    """Return the first template whose pattern matches, or None.

    First match wins, so template order in the catalog matters. More
    specific templates are listed before general ones — 'revenue by
    category' must be tested before the bare 'revenue' pattern, which is
    why the total_revenue pattern carries a negative lookahead excluding
    'by', 'per', and the dimension words.
    """
    q = normalise(question)
    for template in TEMPLATES:
        for pattern in template.patterns:
            if re.search(pattern, q):
                return template
    return None


def run(question: str, con: duckdb.DuckDBPyConnection) -> AgentResult:
    """Match a question to a template and execute its SQL."""
    template = match_template(question)

    if template is None:
        return AgentResult(question=question, matched=False)

    try:
        relation = con.sql(template.sql)
        columns = relation.columns
        rows = relation.fetchall()
    except Exception as exc:  # noqa: BLE001
        return AgentResult(
            question=question,
            matched=True,
            template_name=template.name,
            sql=template.sql,
            error=str(exc),
        )

    return AgentResult(
        question=question,
        matched=True,
        template_name=template.name,
        sql=template.sql,
        measure=template.measure,
        notes=template.notes,
        rows=rows,
        columns=columns,
    )


def format_result(result: AgentResult, max_rows: int = 15) -> str:
    """Render a result for the terminal."""
    lines = [f"Q: {result.question}"]

    if result.refused:
        lines.append("")
        lines.append("  No template matches this question.")
        lines.append("  The baseline agent handles a fixed catalog only; run")
        lines.append("  --list to see what it covers.")
        return "\n".join(lines)

    lines.append(f"   [template: {result.template_name}]")

    if result.error:
        lines.append("")
        lines.append(f"  SQL error: {result.error}")
        return "\n".join(lines)

    if result.measure:
        lines.append(f"   [measure: {result.measure}]")
    if result.notes:
        lines.append(f"   [note: {result.notes}]")

    lines.append("")

    widths = [len(c) for c in result.columns]
    display_rows = result.rows[:max_rows]
    for row in display_rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(f"{value}"))

    header = "  " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(result.columns))
    lines.append(header)
    lines.append("  " + "  ".join("-" * w for w in widths))

    for row in display_rows:
        lines.append(
            "  " + "  ".join(f"{v}".ljust(widths[i]) for i, v in enumerate(row))
        )

    if len(result.rows) > max_rows:
        lines.append(f"  ... {len(result.rows) - max_rows} more rows")

    return "\n".join(lines)


def list_templates() -> str:
    lines = [f"{len(TEMPLATES)} templates in the catalog:", ""]
    for t in TEMPLATES:
        measure = f"  [{t.measure}]" if t.measure else ""
        lines.append(f"  {t.name}{measure}")
        lines.append(f"      e.g. {t.patterns[0]}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Meridian baseline template agent")
    parser.add_argument("question", nargs="*", help="Question to answer")
    parser.add_argument("--list", action="store_true", help="List available templates")
    parser.add_argument("--sql", action="store_true", help="Print the SQL that was run")
    args = parser.parse_args()

    if args.list:
        print(list_templates())
        return

    if not args.question:
        parser.print_help()
        return

    question = " ".join(args.question)
    con = duckdb.connect(str(DB_PATH), read_only=True)

    try:
        result = run(question, con)
        print(format_result(result))
        if args.sql and result.sql:
            print("\n  SQL:")
            for line in result.sql.strip().splitlines():
                print(f"    {line}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
