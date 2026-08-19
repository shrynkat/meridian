"""Generate synthetic customer reviews: unstructured free text with defects."""

import random
from datetime import date, datetime, timedelta

import pandas as pd

from config import (
    SEED,
    RAW_DIR,
    N_REVIEWS,
    START_DATE,
    END_DATE,
    DEFECT_RATES_REVIEWS,
)

random.seed(SEED)

# Phrase banks by sentiment. Reviews are assembled from these so that
# rating and text agree — until inject_defects deliberately breaks that
# agreement on a fraction of rows.
POSITIVE = [
    "Exactly what I was looking for.",
    "Arrived two days early, no complaints at all.",
    "Quality is much better than the price suggests.",
    "Second one I've bought. Would buy again.",
    "Works perfectly, packaging was solid.",
    "Honestly better than the more expensive brand I had before.",
    "My partner liked it so much I ordered another.",
    "Sturdy, well made, and the colour matches the photos.",
]

NEUTRAL = [
    "Does the job. Nothing remarkable either way.",
    "Fine for the price, but I expected slightly better finish.",
    "Works, though the instructions were confusing.",
    "It's okay. Might return it, might not.",
    "Decent, but shipping took longer than stated.",
    "Average product, average experience.",
]

NEGATIVE = [
    "Broke after a week. Very disappointed.",
    "Nothing like the photos, felt cheap in hand.",
    "Arrived damaged and support never replied.",
    "Waste of money, do not recommend.",
    "Stopped working on day three. Requesting a refund.",
    "Wrong item sent, and returning it was a hassle.",
    "Poor quality control — mine came scratched.",
]

# Text quirks that break naive parsing. Real review text is full of these.
QUIRKS = [
    '  Would "recommend" to a friend.',
    " Shipping was fast, packaging, however, was not.",
    "\nEdit: support finally replied, bumping a star.",
    " 10/10 👍",
    " ...still thinking about it.",
]


def load_parents() -> tuple[list[str], list[str], set[tuple[str, str]]]:
    """Read customers, products, and the set of real customer-product pairs."""
    customers = pd.read_csv(RAW_DIR / "customers.csv")
    products = pd.read_csv(RAW_DIR / "products.csv")
    orders = pd.read_csv(RAW_DIR / "orders.csv")

    purchased = set(zip(orders["customer_id"], orders["product_id"]))
    return list(customers["customer_id"]), list(products["product_id"]), purchased


def random_date() -> date:
    span = (END_DATE - START_DATE).days
    return START_DATE + timedelta(days=random.randint(0, span))


def text_for_rating(rating: int) -> str:
    """Pick review text whose sentiment matches the star rating."""
    if rating >= 4:
        body = random.choice(POSITIVE)
    elif rating == 3:
        body = random.choice(NEUTRAL)
    else:
        body = random.choice(NEGATIVE)

    if random.random() < 0.30:
        body += random.choice(QUIRKS)
    return body


def build_reviews(n: int, purchased: set) -> list[dict]:
    """Build n clean reviews, each from a customer who actually bought the product."""
    pairs = list(purchased)
    rows = []
    for i in range(1, n + 1):
        cid, pid = random.choice(pairs)
        # Ratings skew positive, as they do on real platforms.
        rating = random.choices([1, 2, 3, 4, 5], weights=[0.08, 0.07, 0.15, 0.30, 0.40])[0]
        rows.append(
            {
                "review_id": f"REV-{i:06d}",
                "customer_id": cid,
                "product_id": pid,
                "rating": rating,
                "review_text": text_for_rating(rating),
                "review_date": random_date().isoformat(),
                "verified_purchase": True,
                "helpful_votes": random.choices(
                    [0, 1, 2, 5, 12, 40], weights=[0.45, 0.20, 0.15, 0.12, 0.06, 0.02]
                )[0],
            }
        )
    return rows


def inject_defects(rows: list[dict], customer_ids: list[str], product_ids: list[str]) -> None:
    """Corrupt a fraction of rows in place."""
    n = len(rows)
    r = DEFECT_RATES_REVIEWS

    # Empty review bodies: a rating with no text.
    for idx in random.sample(range(n), int(n * r["empty_text"])):
        rows[idx]["review_text"] = ""

    # Ratings outside the valid 1-5 range.
    for idx in random.sample(range(n), int(n * r["rating_out_of_range"])):
        rows[idx]["rating"] = random.choice([0, 6, 10, -1])

    # Sentiment mismatch: five stars attached to an angry review. Only a
    # human or an LLM can catch this — no SQL constraint will.
    for idx in random.sample(range(n), int(n * r["sentiment_mismatch"])):
        if rows[idx]["rating"] >= 4:
            rows[idx]["review_text"] = random.choice(NEGATIVE)
        else:
            rows[idx]["review_text"] = random.choice(POSITIVE)

    # Reviews from customers who never bought the product.
    for idx in random.sample(range(n), int(n * r["unverified_purchase"])):
        rows[idx]["customer_id"] = random.choice(customer_ids)
        rows[idx]["product_id"] = random.choice(product_ids)
        rows[idx]["verified_purchase"] = False

    # Copy-pasted review text: the same body under different review IDs.
    template = random.choice(POSITIVE)
    for idx in random.sample(range(n), int(n * r["duplicate_text"])):
        rows[idx]["review_text"] = template


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    customer_ids, product_ids, purchased = load_parents()

    rows = build_reviews(N_REVIEWS, purchased)
    inject_defects(rows, customer_ids, product_ids)
    random.shuffle(rows)

    frame = pd.DataFrame(rows)
    out_path = RAW_DIR / "reviews.csv"
    frame.to_csv(out_path, index=False)

    print(f"wrote {len(frame):,} rows to {out_path}")
    print(f"  empty review_text:    {(frame['review_text'] == '').sum():,}")
    print(f"  ratings out of range: {(~frame['rating'].between(1, 5)).sum():,}")
    print(f"  unverified purchases: {(~frame['verified_purchase']).sum():,}")
    print(f"  text with newlines:   {frame['review_text'].str.contains(chr(10)).sum():,}")
    print(f"  text with quotes:     {frame['review_text'].str.contains('\"').sum():,}")
    print("  rating distribution:")
    for rating, count in sorted(frame["rating"].value_counts().items()):
        print(f"    {rating:>3}  {count:>6,}")


if __name__ == "__main__":
    main()
