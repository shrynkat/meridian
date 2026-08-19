"""Generate synthetic clickstream events as JSONL with nested properties."""

import json
import random
import uuid
from datetime import date, datetime, timedelta

import pandas as pd

from config import (
    SEED,
    RAW_DIR,
    N_EVENTS,
    START_DATE,
    END_DATE,
    EVENT_TYPES,
    EVENT_WEIGHTS,
    DEVICE_TYPES,
    DEVICE_WEIGHTS,
    DEFECT_RATES_EVENTS,
)

random.seed(SEED)

SEARCH_TERMS = [
    "wireless headphones", "running shoes", "coffee maker", "yoga mat",
    "laptop stand", "water bottle", "desk lamp", "backpack",
    "phone case", "bluetooth speaker", "air fryer", "notebook",
]

PAGES = ["/", "/category/electronics", "/category/apparel", "/search",
         "/cart", "/checkout", "/account", "/deals"]


def load_parents() -> tuple[list[str], list[str]]:
    """Read customer and product IDs so events can reference real entities."""
    customers = pd.read_csv(RAW_DIR / "customers.csv")
    products = pd.read_csv(RAW_DIR / "products.csv")
    return list(customers["customer_id"]), list(products["product_id"])


def random_timestamp() -> datetime:
    """Random timestamp anywhere in the business timeline."""
    span = (END_DATE - START_DATE).days
    day = START_DATE + timedelta(days=random.randint(0, span))
    return datetime.combine(day, datetime.min.time()) + timedelta(
        hours=random.randint(0, 23),
        minutes=random.randint(0, 59),
        seconds=random.randint(0, 59),
    )


def build_properties(event_type: str, product_ids: list[str]) -> dict:
    """Build the nested properties object. Shape varies by event type.

    This is the reason we use JSONL rather than CSV: a page_view and an
    add_to_cart genuinely carry different fields, and flattening them into
    one rectangular schema would leave most columns null on most rows.
    """
    if event_type == "page_view":
        return {
            "page_url": random.choice(PAGES),
            "referrer": random.choice(["google", "direct", "email", "social", None]),
            "load_time_ms": random.randint(120, 3200),
        }

    if event_type == "product_view":
        return {
            "product_id": random.choice(product_ids),
            "scroll_depth_pct": random.randint(10, 100),
            "time_on_page_sec": random.randint(3, 480),
        }

    if event_type in ("add_to_cart", "remove_from_cart"):
        return {
            "product_id": random.choice(product_ids),
            "quantity": random.choices([1, 2, 3], weights=[0.7, 0.2, 0.1])[0],
        }

    if event_type == "checkout_start":
        return {
            "cart_size": random.randint(1, 8),
            "cart_value": round(random.uniform(15.0, 950.0), 2),
            "payment_method": random.choice(["card", "paypal", "apple_pay"]),
        }

    if event_type == "search":
        return {
            "query": random.choice(SEARCH_TERMS),
            "results_count": random.randint(0, 240),
        }

    return {}


def build_events(n: int, customer_ids: list[str], product_ids: list[str]) -> list[dict]:
    """Build n clean event records."""
    rows = []
    for _ in range(n):
        event_type = random.choices(EVENT_TYPES, weights=EVENT_WEIGHTS)[0]
        rows.append(
            {
                "event_id": str(uuid.UUID(int=random.getrandbits(128))),
                "session_id": f"SESS-{random.randint(1, 180_000):07d}",
                "customer_id": random.choice(customer_ids),
                "event_type": event_type,
                "event_ts": random_timestamp().isoformat(sep=" "),
                "device_type": random.choices(DEVICE_TYPES, weights=DEVICE_WEIGHTS)[0],
                "properties": build_properties(event_type, product_ids),
            }
        )
    return rows


def inject_defects(rows: list[dict]) -> None:
    """Corrupt a fraction of rows in place."""
    n = len(rows)
    r = DEFECT_RATES_EVENTS

    # Null session IDs: session stitching will have to cope.
    for idx in random.sample(range(n), int(n * r["null_session"])):
        rows[idx]["session_id"] = None

    # Anonymous traffic: no matching customer. Unlike orders, this is a
    # legitimate business fact, not a data error — visitors who never
    # logged in. The dbt test for this should warn, not fail.
    for idx in random.sample(range(n), int(n * r["orphan_customer"])):
        rows[idx]["customer_id"] = None

    # Empty properties object: event fired without its payload.
    for idx in random.sample(range(n), int(n * r["missing_properties"])):
        rows[idx]["properties"] = {}

    # Malformed timestamps: epoch integers instead of ISO strings.
    for idx in random.sample(range(n), int(n * r["malformed_timestamp"])):
        rows[idx]["event_ts"] = str(random.randint(1_672_531_200, 1_782_000_000))


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    customer_ids, product_ids = load_parents()

    rows = build_events(N_EVENTS, customer_ids, product_ids)
    inject_defects(rows)

    out_path = RAW_DIR / "events.jsonl"
    with open(out_path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    null_session = sum(1 for r in rows if r["session_id"] is None)
    anon = sum(1 for r in rows if r["customer_id"] is None)
    empty_props = sum(1 for r in rows if not r["properties"])
    bad_ts = sum(1 for r in rows if r["event_ts"].isdigit())

    type_counts = {}
    for row in rows:
        type_counts[row["event_type"]] = type_counts.get(row["event_type"], 0) + 1

    size_mb = out_path.stat().st_size / 1_048_576

    print(f"wrote {len(rows):,} events to {out_path} ({size_mb:.1f} MB)")
    print(f"  null session_ids:    {null_session:,}")
    print(f"  anonymous (no cust): {anon:,}")
    print(f"  empty properties:    {empty_props:,}")
    print(f"  malformed timestamps:{bad_ts:,}")
    print("  event type mix:")
    for name, count in sorted(type_counts.items(), key=lambda kv: -kv[1]):
        print(f"    {name:<20} {count:>7,}")


if __name__ == "__main__":
    main()
