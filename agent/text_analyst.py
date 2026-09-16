"""Two-stage analyst: SQL for retrieval, LLM for comprehension.

This answers the questions text-to-SQL cannot. A five-star rating attached
to "Broke after a week. Very disappointed." passes every constraint SQL can
express -- the rating is valid, the text is non-empty, the foreign keys
resolve. Only reading the text finds it.

The pattern differs from the SQL agent deliberately:
  stage 1  a FIXED query (not model-generated) fetches the relevant rows
  stage 2  the model classifies the text in batches
  stage 3  the classifications are aggregated into an answer

Because the generator drew negative review text from a known phrase bank,
ground truth is exact: 508 of 9,983 high-rated reviews carry negative text.
That makes sentiment-mismatch detection a SUPERVISED task with labels, so
the model is scored on precision, recall and F1 rather than assessed by eye.

Usage:
    python agent/text_analyst.py --task mismatch --sample 500
    python agent/text_analyst.py --task mismatch --full
    python agent/text_analyst.py --task complaints
    python agent/text_analyst.py --task category-sentiment
"""

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_agent import call_ollama, DEFAULT_MODEL  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "meridian.duckdb"

BATCH_SIZE = 20

# The generator's negative phrase bank. Review text was drawn from fixed
# lists, so a prefix match recovers the planted label exactly. This is
# ground truth, not a heuristic -- it is only knowable because we wrote
# the generator.
NEGATIVE_PHRASES = [
    "Broke after a week",
    "Nothing like the photos",
    "Arrived damaged",
    "Waste of money",
    "Stopped working on day three",
    "Wrong item sent",
    "Poor quality control",
]


@dataclass
class Classification:
    review_id: str
    rating: int
    text: str
    predicted_negative: bool
    actual_negative: bool


def ground_truth_clause(column: str = "review_text") -> str:
    return " or ".join(f"{column} like '{p}%'" for p in NEGATIVE_PHRASES)


# ---------------------------------------------------------------------------
# Stage 1: retrieval (fixed SQL, not generated)
# ---------------------------------------------------------------------------

def fetch_high_rated(con, limit: int | None) -> list[tuple]:
    """High-rated reviews with text, plus their ground-truth label."""
    gt = ground_truth_clause()
    sql = f"""
        select
            review_id,
            rating,
            review_text,
            ({gt}) as actual_negative
        from silver.stg_reviews
        where rating >= 4
          and review_text is not null
        order by review_id
    """
    if limit:
        # Deterministic sample: every Nth row by review_id, so repeat runs
        # compare against the same population. The step is computed in
        # Python as an integer -- DuckDB's / returns a float, and
        # `rn % 49.915 = 0` is never true, which silently returned zero rows.
        total = con.execute("""
            select count(*) from silver.stg_reviews
            where rating >= 4 and review_text is not null
        """).fetchone()[0]
        step = max(1, total // limit)
        sql = f"""
            select review_id, rating, review_text, actual_negative
            from (
                select *, row_number() over (order by review_id) as rn
                from ({sql})
            )
            where rn % {step} = 0
            limit {limit}
        """
    return con.execute(sql).fetchall()


# ---------------------------------------------------------------------------
# Stage 2: classification
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = """You are classifying the sentiment of product reviews.

For each numbered review below, decide whether the TEXT expresses a negative
experience -- the customer is dissatisfied, the product failed, or they
regret the purchase.

Ignore the star rating entirely. Judge only the words.

Respond with ONLY a JSON array of objects, one per review, in order:
[{{"n": 1, "negative": true}}, {{"n": 2, "negative": false}}]

No explanation. No markdown fences. JSON only.

Reviews:
{reviews}"""


def parse_verdicts(raw: str, expected: int) -> list[bool] | None:
    """Pull a list of booleans out of the model's response."""
    text = raw.strip()

    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return None

    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None

    if not isinstance(parsed, list):
        return None

    verdicts = {}
    for item in parsed:
        if isinstance(item, dict) and "n" in item:
            verdicts[int(item["n"])] = bool(item.get("negative", False))

    if not verdicts:
        return None

    # Any review the model omitted defaults to not-negative, which is the
    # conservative choice: it costs recall rather than inventing positives.
    return [verdicts.get(i + 1, False) for i in range(expected)]


def classify_batch(rows: list[tuple], model: str) -> list[bool]:
    numbered = "\n".join(
        f"{i + 1}. {row[2][:300]}" for i, row in enumerate(rows)
    )
    raw = call_ollama(CLASSIFY_PROMPT.format(reviews=numbered), model)
    verdicts = parse_verdicts(raw, len(rows))
    if verdicts is None:
        return [False] * len(rows)
    return verdicts


# ---------------------------------------------------------------------------
# Stage 3: scoring
# ---------------------------------------------------------------------------

def score(results: list[Classification]) -> dict:
    tp = sum(1 for r in results if r.predicted_negative and r.actual_negative)
    fp = sum(1 for r in results if r.predicted_negative and not r.actual_negative)
    fn = sum(1 for r in results if not r.predicted_negative and r.actual_negative)
    tn = sum(1 for r in results if not r.predicted_negative and not r.actual_negative)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "reviewed": len(results),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "actual_mismatches": tp + fn,
        "predicted_mismatches": tp + fp,
    }


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def task_mismatch(con, model: str, limit: int | None) -> None:
    """Q046: are any of our five-star reviews actually negative?"""
    rows = fetch_high_rated(con, limit)
    print(f"stage 1: fetched {len(rows):,} high-rated reviews with text")
    print(f"stage 2: classifying in batches of {BATCH_SIZE} using {model}\n")

    results: list[Classification] = []
    started = time.perf_counter()
    batches = (len(rows) + BATCH_SIZE - 1) // BATCH_SIZE

    for b in range(batches):
        batch = rows[b * BATCH_SIZE:(b + 1) * BATCH_SIZE]
        verdicts = classify_batch(batch, model)
        for row, verdict in zip(batch, verdicts):
            results.append(Classification(
                review_id=row[0], rating=row[1], text=row[2],
                predicted_negative=verdict, actual_negative=bool(row[3]),
            ))
        done = len(results)
        rate = done / (time.perf_counter() - started)
        eta = (len(rows) - done) / rate if rate else 0
        print(f"\r  {done:,}/{len(rows):,} reviews   {rate:.0f}/s   eta {eta / 60:.1f}m",
              end="", flush=True)

    elapsed = time.perf_counter() - started
    print(f"\n\nstage 3: scoring against ground truth\n")

    if not results:
        print("  no reviews fetched — check the retrieval query")
        return

    metrics = score(results)
    metrics["elapsed_s"] = round(elapsed, 1)
    metrics["model"] = model

    print(f"  reviewed              {metrics['reviewed']:,}")
    print(f"  actual mismatches     {metrics['actual_mismatches']:,} "
          f"({metrics['actual_mismatches'] / metrics['reviewed'] * 100:.2f}%)")
    print(f"  predicted mismatches  {metrics['predicted_mismatches']:,}")
    print()
    print(f"  true positives        {metrics['true_positives']:,}")
    print(f"  false positives       {metrics['false_positives']:,}")
    print(f"  false negatives       {metrics['false_negatives']:,}")
    print()
    print(f"  precision             {metrics['precision']:.3f}")
    print(f"  recall                {metrics['recall']:.3f}")
    print(f"  F1                    {metrics['f1']:.3f}")
    print()
    print(f"  elapsed               {elapsed / 60:.1f} min")

    missed = [r for r in results if r.actual_negative and not r.predicted_negative][:3]
    if missed:
        print("\n  missed examples:")
        for r in missed:
            print(f"    [{r.rating}*] {r.text[:70]}")

    false_pos = [r for r in results if r.predicted_negative and not r.actual_negative][:3]
    if false_pos:
        print("\n  false positive examples:")
        for r in false_pos:
            print(f"    [{r.rating}*] {r.text[:70]}")

    out = ROOT / "eval" / "results" / "text_mismatch.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "task": "sentiment_mismatch",
        "metrics": metrics,
        # Every disagreement is saved, not just the first 100 rows. The
        # false positives turned out to be a LABELLING artifact rather than
        # model error -- reviews whose appended quirk text genuinely
        # contains a complaint -- so they must all be inspectable.
        "disagreements": [
            {"review_id": r.review_id, "rating": r.rating,
             "predicted": r.predicted_negative, "actual": r.actual_negative,
             "text": r.text[:200]}
            for r in results
            if r.predicted_negative != r.actual_negative
        ],
        "sample": [
            {"review_id": r.review_id, "rating": r.rating,
             "predicted": r.predicted_negative, "actual": r.actual_negative,
             "text": r.text[:120]}
            for r in results[:100]
        ],
    }, indent=2))
    print(f"\n  saved to {out.relative_to(ROOT)}")


def task_complaints(con, model: str) -> None:
    """Q047: what are customers complaining about most?"""
    rows = con.execute("""
        select review_text
        from silver.stg_reviews
        where rating <= 2 and review_text is not null
        order by helpful_votes desc
        limit 120
    """).fetchall()

    print(f"stage 1: fetched {len(rows)} low-rated reviews\n")

    sample = "\n".join(f"- {r[0][:200]}" for r in rows)
    prompt = (
        "Read these negative product reviews and identify the recurring "
        "complaint themes. List the top 5 themes, each with a one-line "
        "description and roughly how common it appears to be. Be specific "
        "about what customers actually say.\n\n"
        f"Reviews:\n{sample}"
    )
    print("stage 2: summarising themes\n")
    print(call_ollama(prompt, model))


def task_category_sentiment(con, model: str) -> None:
    """Q048: summarise sentiment for the worst-rated category."""
    worst = con.execute("""
        select p.category, round(avg(r.rating), 2) as avg_rating, count(*) as n
        from silver.stg_reviews r
        join gold.dim_products p on r.product_id = p.product_id
        where p.category is not null
        group by 1 order by avg_rating asc limit 1
    """).fetchone()

    print(f"stage 1: worst-rated category is {worst[0]} "
          f"(avg {worst[1]}, {worst[2]:,} reviews)\n")

    rows = con.execute("""
        select r.review_text
        from silver.stg_reviews r
        join gold.dim_products p on r.product_id = p.product_id
        where p.category = ?
          and r.review_text is not null
        order by r.rating asc
        limit 100
    """, [worst[0]]).fetchall()

    sample = "\n".join(f"- {r[0][:180]}" for r in rows)
    prompt = (
        f"Summarise the sentiment in these reviews for the {worst[0]} "
        f"category in 4-5 sentences. What do customers like, what do they "
        f"dislike, and is there a pattern?\n\nReviews:\n{sample}"
    )
    print("stage 2: summarising\n")
    print(call_ollama(prompt, model))


def main() -> None:
    ap = argparse.ArgumentParser(description="Meridian text comprehension analyst")
    ap.add_argument("--task", choices=["mismatch", "complaints", "category-sentiment"],
                    default="mismatch")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--sample", type=int, default=500)
    ap.add_argument("--full", action="store_true", help="Classify all reviews")
    args = ap.parse_args()

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        if args.task == "mismatch":
            task_mismatch(con, args.model, None if args.full else args.sample)
        elif args.task == "complaints":
            task_complaints(con, args.model)
        else:
            task_category_sentiment(con, args.model)
    finally:
        con.close()


if __name__ == "__main__":
    main()
