"""Generate synthetic orders referencing existing customers and products."""

import random
from datetime import date, datetime, timedelta

import pandas as pd

from config import (
    SEED,
    RAW_DIR,
    N_ORDERS,
    START_DATE,
    END_DATE,
    ORDER_STATUSES,
    STATUS_WEIGHTS,
    DEFECT_RATES_ORDERS,
)

random.seed(SEED)


def load_parents() -> tuple[dict[str, date], list[str], dict[str, float]]:
    """Read customers and products so orders can reference real IDs."""
    customers = pd.read_csv(RAW_DIR / "customers.csv")
    products = pd.read_csv(RAW_DIR / "products.csv")

    # Signup dates come in two formats thanks to our own defect injection,
    # so parse both. This is the first time our messy data bites us — which
    # is the point.
    signup = {}
    for cid, raw in zip(customers["customer_id"], customers["signup_date"]):
        try:
            parsed = date.fromisoformat(str(raw))
        except ValueError:
            parsed = datetime.strptime(str(raw), "%m/%d/%Y").date()
        signup[cid] = parsed

    prices = dict(zip(products["product_id"], products["price"].abs()))
    return signup, list(products["product_id"]), prices


def random_datetime_after(earliest: date) -> datetime:
    """Random timestamp between a floor date and the global end date."""
    floor = max(earliest, START_DATE)
    span = (END_DATE - floor).days
    day = floor + timedelta(days=random.randint(0, max(span, 0)))
    return datetime.combine(day, datetime.min.time()) + timedelta(
        hours=random.randint(0, 23), minutes=random.randint(0, 59)
    )


def build_orders(n, signup, product_ids, prices) -> list[dict]:
    """Build n clean orders, each dated after its customer signed up."""
    customer_ids = list(signup)
    rows = []
    for i in range(1, n + 1):
        cid = random.choice(customer_ids)
        pid = random.choice(product_ids)
        qty = random.choices([1, 2, 3, 4, 5], weights=[0.55, 0.25, 0.10, 0.06, 0.04])[0]
        unit_price = prices[pid]
        rows.append(
            {
                "order_id": f"ORD-{i:07d}",
                "customer_id": cid,
                "product_id": pid,
                "quantity": qty,
                "unit_price": round(unit_price, 2),
                "order_total": round(unit_price * qty, 2),
                "order_ts": random_datetime_after(signup[cid]).isoformat(sep=" "),
                "status": random.choices(ORDER_STATUSES, weights=STATUS_WEIGHTS)[0],
            }
        )
    return rows


def inject_defects(rows, signup) -> list[dict]:
    """Break referential integrity and business rules on a fraction of rows."""
    n = len(rows)
    r = DEFECT_RATES_ORDERS

    # Orphans: foreign keys pointing at IDs that do not exist.
    for idx in random.sample(range(n), int(n * r["orphan_customer"])):
        rows[idx]["customer_id"] = f"CUST-{random.randint(900000, 999999):06d}"

    for idx in random.sample(range(n), int(n * r["orphan_product"])):
        rows[idx]["product_id"] = f"PROD-{random.randint(90000, 99999):05d}"

    # Timeline violation: order placed before the customer existed.
    for idx in random.sample(range(n), int(n * r["order_before_signup"])):
        cid = rows[idx]["customer_id"]
        if cid in signup:
            earlier = signup[cid] - timedelta(days=random.randint(1, 400))
            rows[idx]["order_ts"] = datetime.combine(
                earlier, datetime.min.time()
            ).isoformat(sep=" ")

    # Absurd quantities: valid integers, implausible facts.
    for idx in random.sample(range(n), int(n * r["extreme_quantity"])):
        rows[idx]["quantity"] = random.randint(500, 9999)
        rows[idx]["order_total"] = round(
            rows[idx]["unit_price"] * rows[idx]["quantity"], 2
        )

    # Null quantities, with order_total left intact so the two disagree.
    for idx in random.sample(range(n), int(n * r["null_quantity"])):
        rows[idx]["quantity"] = None

    # Duplicate primary keys: same order_id, different row.
    dupes = [
        dict(rows[i])
        for i in random.sample(range(n), int(n * r["duplicate_order_id"]))
    ]
    return rows + dupes


def main() -> None:
    signup, product_ids, prices = load_parents()

    rows = build_orders(N_ORDERS, signup, product_ids, prices)
    rows = inject_defects(rows, signup)
    random.shuffle(rows)

    frame = pd.DataFrame(rows)
    out_path = RAW_DIR / "orders.csv"
    frame.to_csv(out_path, index=False)

    valid_customers = set(signup)
    valid_products = set(product_ids)

    print(f"wrote {len(frame):,} rows to {out_path}")
    print(f"  orphan customer_ids: {(~frame['customer_id'].isin(valid_customers)).sum():,}")
    print(f"  orphan product_ids:  {(~frame['product_id'].isin(valid_products)).sum():,}")
    print(f"  null quantities:     {frame['quantity'].isna().sum():,}")
    print(f"  quantity > 100:      {(frame['quantity'] > 100).sum():,}")
    print(f"  duplicate order_ids: {frame['order_id'].duplicated().sum():,}")
    print(f"  status mix:\n{frame['status'].value_counts().to_string()}")


if __name__ == "__main__":
    main()
