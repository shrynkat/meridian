"""Text-to-SQL agent backed by a local model through Ollama.

The model is given the semantic layer and the question. It is never given
any data — schema in, SQL out. Generated SQL runs against a READ-ONLY
DuckDB connection, so a destructive statement fails at the database layer
regardless of what the model produced and regardless of whether any other
check caught it.

On a SQL error the error text is fed back once with the original question.
A model that sees "Referenced column X not found" usually fixes it.
Measuring first-attempt accuracy alone understates what the system does.

Usage:
    python agent/llm_agent.py "what is our total revenue"
    python agent/llm_agent.py --model sqlcoder:7b "revenue by category"
    python agent/llm_agent.py --show-prompt "how many orders"
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path

import duckdb
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from guardrails import Guardrail  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "meridian.duckdb"
SEMANTIC_PATH = PROJECT_ROOT / "semantic" / "semantic_layer.yml"

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_TIMEOUT = 120

# Statements the agent will not run. The read-only connection already
# blocks these, but failing before execution gives a clearer signal and
# means the check is visible in the transcript rather than buried in a
# database error.
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|truncate|attach|copy|"
    r"export|install|load|pragma|set)\b",
    re.IGNORECASE,
)


@dataclass
class LLMResult:
    question: str
    model: str
    sql: str | None = None
    raw_output: str = ""
    executed: bool = False
    rows: list = field(default_factory=list)
    columns: list = field(default_factory=list)
    error: str | None = None
    retried: bool = False
    refused: bool = False
    refusal_reason: str | None = None
    latency_s: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rows"] = [[str(v) for v in row] for row in self.rows[:50]]
        return d


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_schema_context(spec: dict) -> str:
    """Condense the semantic layer into a prompt-sized schema description.

    The full YAML is ~457 lines. Most of it is prose a model does not need.
    What it does need: table names, grain, which questions each answers,
    column names with the cautions attached, and the metric definitions.
    """
    parts = []

    d = spec["defaults"]
    parts.append("## Rules")
    parts.append(f"- Default revenue measure: {d['revenue_measure']}")
    for req in d["answer_requirements"]:
        parts.append(f"- {' '.join(req.split())}")
    parts.append("")

    parts.append("## Tables")
    for t in spec["tables"]:
        parts.append(f"\n### {t['name']}")
        parts.append(f"GRAIN: {t['grain']}")
        if t.get("description"):
            parts.append(" ".join(t["description"].split()))
        if t.get("answers_questions"):
            parts.append("Use for: " + "; ".join(t["answers_questions"]))
        if t.get("does_not_answer"):
            parts.append("Do NOT use for: " + "; ".join(t["does_not_answer"]))

        cols = t.get("columns") or {}
        if cols:
            parts.append("Columns:")
            for name, meta in cols.items():
                line = f"  {name} ({meta.get('type', '?')})"
                if meta.get("allowed_values"):
                    line += f" one of {meta['allowed_values']}"
                if meta.get("description"):
                    line += " -- " + " ".join(meta["description"].split())
                parts.append(line)

        if t.get("caution"):
            parts.append("CAUTION: " + " ".join(t["caution"].split()))
        if t.get("known_limitation"):
            parts.append("LIMITATION: " + " ".join(t["known_limitation"].split()))

    parts.append("\n## Metrics")
    for m in spec["metrics"]:
        line = f"- {m['name']}: {m.get('default_expression', 'see description')}"
        if m.get("default_table"):
            line += f" FROM {m['default_table']}"
        if m.get("default_filter"):
            line += f" WHERE {m['default_filter']}"
        parts.append(line)

    parts.append("\n## Joins")
    for j in spec["joins"]:
        line = f"- {j['from']} -> {j['to']} on {j['join_key']} ({j['cardinality']})"
        if j.get("warning"):
            line += f" WARNING: {j['warning']}"
        parts.append(line)

    parts.append("\n## Known data conditions (do not report these as findings)")
    for c in spec["known_conditions"]:
        parts.append(f"- {c}")

    return "\n".join(parts)


SYSTEM_PROMPT = """You translate business questions into DuckDB SQL.

Output rules, in order of importance:
1. Output ONLY a SQL query. No explanation, no markdown fences, no commentary.
2. SELECT statements only. Never INSERT, UPDATE, DELETE, DROP, CREATE, or ALTER.
3. Use only the tables and columns listed in the schema below.
4. Respect each table's stated GRAIN. Counting orders from a line-item table
   counts line items instead.
5. Apply the default revenue measure unless the question asks otherwise.

If the question cannot be answered from these tables, output exactly:
CANNOT_ANSWER: <one line saying why>

If the question asks you to modify data, output exactly:
REFUSED: this agent is read-only

{schema}
"""

RETRY_PROMPT = """The SQL you produced failed with this error:

{error}

The failed query was:
{sql}

Write a corrected SQL query.

If the error names candidate bindings, those are the columns that actually
exist on that table — use them. If a column you wanted does not exist,
compute it with an aggregate instead of selecting it. Re-read the schema and
pick the table whose GRAIN matches what the question asks for.

Output ONLY the corrected SQL statement. Do not explain. Do not output
CANNOT_ANSWER — the question is answerable, the previous query was simply
wrong."""


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

def call_ollama(prompt: str, model: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Send a prompt to the local Ollama server and return the response text."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        # Low temperature: SQL generation wants determinism, not creativity.
        "options": {"temperature": 0.0, "num_predict": 512},
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())["response"]
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Cannot reach Ollama at {OLLAMA_URL}. Is `ollama serve` running? ({exc})"
        ) from exc


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

def extract_sql(raw: str) -> tuple[str | None, str | None]:
    """Pull a SQL statement out of model output.

    Returns (sql, refusal_reason). Models wrap SQL in markdown fences, prefix
    it with prose, append explanations, or emit several statements. This is
    unglamorous and it is where most failures live.
    """
    text = raw.strip()

    if text.upper().startswith("REFUSED"):
        return None, text

    # A CANNOT_ANSWER prefix is only a refusal if there is genuinely no SQL
    # in the output. Models sometimes prefix a correct diagnosis with it and
    # then name the right table anyway — discarding that loses a usable
    # answer.
    if text.upper().startswith("CANNOT_ANSWER"):
        if not re.search(r"\b(select|with)\b", text, re.IGNORECASE):
            return None, text

    # Strip markdown fences, with or without a language tag.
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    # Drop leading prose: start at the first SELECT or WITH.
    start = re.search(r"\b(select|with)\b", text, re.IGNORECASE)
    if not start:
        return None, f"no SQL found in output: {raw[:120]}"
    text = text[start.start():]

    # Take the first statement only.
    if ";" in text:
        text = text.split(";")[0]

    return text.strip(), None


def check_forbidden(sql: str) -> str | None:
    """Reject write operations before they reach the database.

    The read-only connection blocks these anyway. Checking here makes the
    refusal explicit and visible rather than surfacing as a database error.
    """
    body = re.sub(r"--[^\n]*", "", sql)
    match = FORBIDDEN.search(body)
    if match:
        return f"query contains forbidden keyword: {match.group(0).upper()}"
    return None


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def run(
    question: str,
    con: duckdb.DuckDBPyConnection,
    schema_context: str,
    guard: Guardrail | None = None,
    model: str = DEFAULT_MODEL,
    allow_retry: bool = True,
) -> LLMResult:
    started = time.perf_counter()
    guard = guard or Guardrail()
    result = LLMResult(question=question, model=model)

    prompt = SYSTEM_PROMPT.format(schema=schema_context) + f"\n\nQuestion: {question}\nSQL:"

    try:
        raw = call_ollama(prompt, model)
    except RuntimeError as exc:
        result.error = str(exc)
        result.latency_s = time.perf_counter() - started
        return result

    result.raw_output = raw
    sql, refusal = extract_sql(raw)

    if refusal:
        result.refused = True
        result.refusal_reason = refusal
        result.latency_s = time.perf_counter() - started
        return result

    verdict = guard.check(sql, question)
    if not verdict.allowed:
        result.sql = sql
        # A structural or semantic block is not a refusal — it is a
        # correctable error. Route it through the retry path with the
        # guardrail's hint, which names the exact fix.
        if allow_retry and verdict.retry_hint:
            first_error = verdict.blocked_reason
            result.retried = True
            retry_prompt = (
                SYSTEM_PROMPT.format(schema=schema_context)
                + f"\n\nQuestion: {question}\n\n"
                + RETRY_PROMPT.format(error=first_error + " " + verdict.retry_hint, sql=sql)
                + "\nSQL:"
            )
            try:
                raw2 = call_ollama(retry_prompt, model)
            except RuntimeError as exc2:
                result.error = f"{first_error} | retry failed: {exc2}"
                result.latency_s = time.perf_counter() - started
                return result

            result.raw_output = raw + "\n---RETRY---\n" + raw2
            sql2, refusal2 = extract_sql(raw2)
            if refusal2 or sql2 is None:
                result.error = f"{first_error} | retry produced no SQL"
                result.latency_s = time.perf_counter() - started
                return result

            verdict2 = guard.check(sql2, question)
            if not verdict2.allowed:
                result.sql = sql2
                result.error = f"blocked after retry: {verdict2.blocked_reason}"
                result.latency_s = time.perf_counter() - started
                return result
            sql = sql2
        else:
            result.refused = True
            result.refusal_reason = verdict.blocked_reason
            result.latency_s = time.perf_counter() - started
            return result

    result.sql = sql

    try:
        rel = con.sql(sql)
        result.columns = rel.columns
        result.rows = rel.fetchall()
        result.executed = True
    except Exception as exc:  # noqa: BLE001
        first_error = str(exc)

        if not allow_retry:
            result.error = first_error
            result.latency_s = time.perf_counter() - started
            return result

        result.retried = True
        retry_prompt = (
            SYSTEM_PROMPT.format(schema=schema_context)
            + f"\n\nQuestion: {question}\n\n"
            + RETRY_PROMPT.format(error=first_error, sql=sql)
            + "\nSQL:"
        )

        try:
            raw2 = call_ollama(retry_prompt, model)
        except RuntimeError as exc2:
            result.error = f"{first_error} | retry failed: {exc2}"
            result.latency_s = time.perf_counter() - started
            return result

        result.raw_output = raw + "\n---RETRY---\n" + raw2
        sql2, refusal2 = extract_sql(raw2)

        if refusal2 or sql2 is None:
            result.error = f"{first_error} | retry produced no SQL"
            result.latency_s = time.perf_counter() - started
            return result

        forbidden2 = check_forbidden(sql2)
        if forbidden2:
            result.sql = sql2
            result.refused = True
            result.refusal_reason = forbidden2
            result.latency_s = time.perf_counter() - started
            return result

        result.sql = sql2
        try:
            rel = con.sql(sql2)
            result.columns = rel.columns
            result.rows = rel.fetchall()
            result.executed = True
        except Exception as exc3:  # noqa: BLE001
            result.error = f"{first_error} | retry: {exc3}"

    result.latency_s = time.perf_counter() - started
    return result


def format_result(result: LLMResult, max_rows: int = 15) -> str:
    lines = [f"Q: {result.question}", f"   [model: {result.model}  {result.latency_s:.1f}s]"]

    if result.refused:
        lines.append("")
        lines.append(f"  REFUSED: {result.refusal_reason}")
        return "\n".join(lines)

    if result.sql:
        lines.append("")
        for line in result.sql.splitlines():
            lines.append(f"   | {line}")
        lines.append("")

    if result.retried:
        lines.append("   [retried after error]")

    if result.error:
        lines.append(f"  ERROR: {result.error}")
        return "\n".join(lines)

    if not result.rows:
        lines.append("  (no rows)")
        return "\n".join(lines)

    widths = [len(c) for c in result.columns]
    shown = result.rows[:max_rows]
    for row in shown:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(f"{v}"))

    lines.append("  " + "  ".join(c.ljust(widths[i]) for i, c in enumerate(result.columns)))
    lines.append("  " + "  ".join("-" * w for w in widths))
    for row in shown:
        lines.append("  " + "  ".join(f"{v}".ljust(widths[i]) for i, v in enumerate(row)))

    if len(result.rows) > max_rows:
        lines.append(f"  ... {len(result.rows) - max_rows} more rows")

    return "\n".join(lines)


def load_schema_context() -> str:
    spec = yaml.safe_load(SEMANTIC_PATH.read_text())
    return build_schema_context(spec)


def main() -> None:
    parser = argparse.ArgumentParser(description="Meridian LLM text-to-SQL agent")
    parser.add_argument("question", nargs="*")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--show-prompt", action="store_true")
    parser.add_argument("--no-retry", action="store_true")
    parser.add_argument("--raw", action="store_true", help="Print raw model output")
    args = parser.parse_args()

    schema_context = load_schema_context()

    if args.show_prompt:
        print(SYSTEM_PROMPT.format(schema=schema_context))
        print(f"\n[prompt is {len(schema_context.split())} words]")
        if not args.question:
            return

    if not args.question:
        parser.print_help()
        return

    question = " ".join(args.question)
    con = duckdb.connect(str(DB_PATH), read_only=True)

    try:
        result = run(question, con, schema_context, model=args.model,
                     allow_retry=not args.no_retry)
        print(format_result(result))
        if args.raw:
            print("\n--- raw model output ---")
            print(result.raw_output)
    finally:
        con.close()


if __name__ == "__main__":
    main()
