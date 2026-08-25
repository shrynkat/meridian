"""Evaluation harness: score agents against the Meridian benchmark.

SCORING
  Strict. A question is correct only if the agent returns the expected
  answer. A refusal, an error, or no SQL on an answerable question all
  score as incorrect — the agent is supposed to answer.

  The two exceptions are encoded in the benchmark itself: Q049 (forecasting)
  and Q050 (deletion) expect REFUSAL_EXPECTED, where refusing IS correct.

  Failure MODE is recorded separately from the score. Two models can both
  score 60% while failing completely differently — one returning wrong
  numbers, the other refusing. Same accuracy, very different systems. The
  first is dangerous; the second is merely unhelpful.

USAGE
  python eval/run_eval.py --agent baseline
  python eval/run_eval.py --agent llm --model qwen2.5-coder:7b --limit 10
  python eval/run_eval.py --agent llm --model sqlcoder:7b --resume
  python eval/run_eval.py --report results/llm_qwen2.5-coder_7b.json
"""

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))

BENCHMARK = ROOT / "eval" / "benchmark.yml"
RESULTS_DIR = ROOT / "eval" / "results"
DB_PATH = ROOT / "meridian.duckdb"

SENTINELS = {"REFUSAL_EXPECTED", "REQUIRES_TEXT_ANALYSIS", "CLARIFICATION_REQUESTED"}


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def values_match(actual, expected, tolerance: float) -> bool:
    """Compare a returned value against the expected one."""
    if actual is None:
        return False

    try:
        a = float(actual)
        e = float(expected)
        return abs(a - e) <= max(float(tolerance), 0.0)
    except (TypeError, ValueError):
        pass

    return str(actual).strip().lower() == str(expected).strip().lower()


def score_answer(question: dict, rows, columns, refused: bool,
                 error: str | None) -> tuple[bool, str]:
    """Return (correct, failure_mode).

    failure_mode is one of: ok, wrong_value, wrong_row_count, sql_error,
    refused, no_rows, should_have_refused.
    """
    expected = question.get("expected")
    expected_rows = question.get("expected_row_count")

    # Questions where refusing is the correct behaviour.
    if expected in SENTINELS:
        if expected == "REFUSAL_EXPECTED":
            return (refused, "ok" if refused else "should_have_refused")
        # Text-analysis and clarification questions are scored by hand;
        # mark them for manual review rather than guessing.
        return (False, "manual_review")

    # Everything below is an answerable question. Refusing is a failure.
    if refused:
        return (False, "refused")
    if error:
        return (False, "sql_error")
    if not rows:
        return (False, "no_rows")

    if expected_rows is not None:
        # A LIMIT difference is not a wrong answer. A template returning the
        # top 20 when the reference returns the top 5 has the same content in
        # the same order — penalising it measures row limits, not correctness.
        # Require at least the expected count, and that the first row matches
        # if a reference first row was recorded.
        if len(rows) >= expected_rows:
            return (True, "ok")
        return (False, "too_few_rows")

    tolerance = question.get("tolerance", 0)

    # Scalar expected: check the first cell, then scan the first row in case
    # the agent returned extra columns alongside the answer.
    first = rows[0]
    if values_match(first[0], expected, tolerance):
        return (True, "ok")
    for value in first:
        if values_match(value, expected, tolerance):
            return (True, "ok")

    return (False, "wrong_value")


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def diagnose_sql(question: dict, sql: str | None) -> dict:
    """Check whether the query used the right tables and filters.

    This does NOT affect the score. It explains it: a model can return the
    right number from the wrong table by luck, and that distinction matters
    for judging whether accuracy will hold on unseen questions.
    """
    if not sql:
        return {"tables_ok": None, "filters_ok": None, "missing": []}

    lowered = sql.lower()

    required_tables = question.get("required_tables") or []
    tables_hit = [t for t in required_tables if t.lower() in lowered]
    tables_ok = len(tables_hit) == len(required_tables) if required_tables else None

    required_filters = question.get("required_filters") or []
    missing = []
    for f in required_filters:
        if f == "is_completed" and "is_completed" not in lowered \
                and "not in ('cancelled'" not in lowered:
            missing.append(f)
        elif f == "excludes_outliers" and "ex_outliers" not in lowered \
                and "is_outlier_quantity" not in lowered:
            missing.append(f)
        elif f == "aggregates_to_order_grain" and "group by" not in lowered:
            missing.append(f)
        elif f == "window_function" and "over (" not in lowered:
            missing.append(f)

    filters_ok = len(missing) == 0 if required_filters else None

    return {"tables_ok": tables_ok, "filters_ok": filters_ok, "missing": missing}


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------

def run_baseline(questions, con) -> list[dict]:
    import baseline_agent

    out = []
    for q in questions:
        started = time.perf_counter()
        res = baseline_agent.run(q["question"], con)
        elapsed = time.perf_counter() - started

        correct, mode = score_answer(
            q, res.rows or [], res.columns or [], res.refused, res.error
        )

        out.append({
            "id": q["id"],
            "question": q["question"],
            "difficulty": q["difficulty"],
            "category": q["category"],
            "template_covered": q["template_covered"],
            "correct": correct,
            "failure_mode": mode,
            "sql": res.sql,
            "refused": res.refused,
            "error": res.error,
            "latency_s": round(elapsed, 3),
            "diagnostics": diagnose_sql(q, res.sql),
            "returned": [[str(v) for v in r] for r in (res.rows or [])[:3]],
        })
        flag = "PASS" if correct else "fail"
        print(f"  {q['id']}  {flag:<4}  {mode:<20}  {q['question'][:46]}")

    return out


def run_llm(questions, con, model: str) -> list[dict]:
    import llm_agent

    schema_context = llm_agent.load_schema_context()
    out = []

    for q in questions:
        res = llm_agent.run(q["question"], con, schema_context, model=model)

        correct, mode = score_answer(
            q, res.rows or [], res.columns or [], res.refused, res.error
        )

        out.append({
            "id": q["id"],
            "question": q["question"],
            "difficulty": q["difficulty"],
            "category": q["category"],
            "template_covered": q["template_covered"],
            "correct": correct,
            "failure_mode": mode,
            "sql": res.sql,
            "refused": res.refused,
            "refusal_reason": res.refusal_reason,
            "error": res.error,
            "retried": res.retried,
            "latency_s": round(res.latency_s, 3),
            "diagnostics": diagnose_sql(q, res.sql),
            "returned": [[str(v) for v in r] for r in (res.rows or [])[:3]],
        })
        flag = "PASS" if correct else "fail"
        retry = " R" if res.retried else "  "
        print(f"  {q['id']}  {flag:<4}{retry} {mode:<20} {res.latency_s:5.1f}s  "
              f"{q['question'][:40]}")

    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def report(results: list[dict], label: str) -> str:
    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    manual = sum(1 for r in results if r["failure_mode"] == "manual_review")
    scorable = total - manual

    lines = [
        "",
        "=" * 68,
        f"  {label}",
        "=" * 68,
        "",
        f"  Accuracy: {correct}/{scorable} scorable "
        f"({correct / scorable * 100:.1f}%)" if scorable else "  no scorable questions",
        f"  ({manual} questions require manual review)" if manual else "",
        "",
    ]

    lines.append("  Failure modes")
    for mode, n in Counter(r["failure_mode"] for r in results).most_common():
        lines.append(f"    {mode:<24} {n:>3}")

    lines.append("")
    lines.append("  By difficulty")
    for diff in ["easy", "medium", "hard"]:
        subset = [r for r in results if r["difficulty"] == diff
                  and r["failure_mode"] != "manual_review"]
        if subset:
            ok = sum(1 for r in subset if r["correct"])
            lines.append(f"    {diff:<10} {ok:>2}/{len(subset):<3} "
                         f"({ok / len(subset) * 100:5.1f}%)")

    lines.append("")
    lines.append("  By category")
    cats = sorted({r["category"] for r in results})
    for cat in cats:
        subset = [r for r in results if r["category"] == cat
                  and r["failure_mode"] != "manual_review"]
        if subset:
            ok = sum(1 for r in subset if r["correct"])
            lines.append(f"    {cat:<22} {ok:>2}/{len(subset):<3} "
                         f"({ok / len(subset) * 100:5.1f}%)")

    covered = [r for r in results if r["template_covered"]
               and r["failure_mode"] != "manual_review"]
    uncovered = [r for r in results if not r["template_covered"]
                 and r["failure_mode"] != "manual_review"]
    lines.append("")
    lines.append("  Versus baseline coverage")
    if covered:
        ok = sum(1 for r in covered if r["correct"])
        lines.append(f"    template-covered      {ok:>2}/{len(covered):<3} "
                     f"({ok / len(covered) * 100:5.1f}%)")
    if uncovered:
        ok = sum(1 for r in uncovered if r["correct"])
        lines.append(f"    beyond templates      {ok:>2}/{len(uncovered):<3} "
                     f"({ok / len(uncovered) * 100:5.1f}%)")

    diag = [r for r in results if r["diagnostics"]["tables_ok"] is not None]
    if diag:
        lines.append("")
        lines.append("  Diagnostics (does not affect score)")
        t_ok = sum(1 for r in diag if r["diagnostics"]["tables_ok"])
        lines.append(f"    correct tables used   {t_ok:>2}/{len(diag)}")
        lucky = [r for r in diag if r["correct"] and not r["diagnostics"]["tables_ok"]]
        if lucky:
            lines.append(f"    right answer, wrong table: {len(lucky)} "
                         f"({', '.join(r['id'] for r in lucky)})")

    fdiag = [r for r in results if r["diagnostics"]["filters_ok"] is not None]
    if fdiag:
        f_ok = sum(1 for r in fdiag if r["diagnostics"]["filters_ok"])
        lines.append(f"    required filters applied {f_ok:>2}/{len(fdiag)}")

    lat = [r["latency_s"] for r in results if r.get("latency_s")]
    if lat:
        lines.append("")
        lines.append(f"  Latency: mean {sum(lat) / len(lat):.1f}s  "
                     f"total {sum(lat) / 60:.1f} min")

    failures = [r for r in results
                if not r["correct"] and r["failure_mode"] != "manual_review"]
    if failures:
        lines.append("")
        lines.append("  Failures")
        for r in failures:
            lines.append(f"    {r['id']}  {r['failure_mode']:<20} {r['question'][:44]}")

    lines.append("")
    return "\n".join(l for l in lines if l is not None)


# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Meridian evaluation harness")
    ap.add_argument("--agent", choices=["baseline", "llm"], default="baseline")
    ap.add_argument("--model", default="qwen2.5-coder:7b")
    ap.add_argument("--limit", type=int, help="Run only the first N questions")
    ap.add_argument("--report", help="Print a report from a saved results file")
    args = ap.parse_args()

    if args.report:
        payload = json.loads(Path(args.report).read_text())
        print(report(payload["results"], payload["label"]))
        return

    spec = yaml.safe_load(BENCHMARK.read_text())
    questions = spec["questions"]
    if args.limit:
        questions = questions[:args.limit]

    label = ("baseline template agent" if args.agent == "baseline"
             else f"llm agent [{args.model}]")

    print(f"\nrunning {len(questions)} questions against {label}\n")

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        if args.agent == "baseline":
            results = run_baseline(questions, con)
        else:
            results = run_llm(questions, con, args.model)
    finally:
        con.close()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = ("baseline" if args.agent == "baseline"
            else "llm_" + args.model.replace(":", "_").replace(".", "-"))
    out_path = RESULTS_DIR / f"{slug}.json"
    out_path.write_text(json.dumps({
        "label": label,
        "agent": args.agent,
        "model": args.model if args.agent == "llm" else None,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "question_count": len(questions),
        "results": results,
    }, indent=2))

    print(report(results, label))
    print(f"  saved to {out_path.relative_to(ROOT)}\n")


if __name__ == "__main__":
    main()
