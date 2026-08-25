"""Generate synthetic orders as two tables: headers and line items.

Real e-commerce orders contain multiple products. Splitting orders into a
header table (one row per order) and a line-item table (one row per product
on that order) is how warehouses actually model this, and it introduces the
grain problems that make dimensional modelling non-trivial:

  - joining the two tables and summing order_total double-counts revenue
  - "how many orders" and "how many items" are different questions
  - basket analysis is only possible at the line grain

The generator plants defects that exercise exactly these seams.
"""

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

# Basket size distribution. Weighted toward small baskets, with a tail —
# uniform would be unrealistic and would make average basket size useless.
BASKET_SIZES = [1, 2, 3, 4, 5, 6, 7, 8]
BASKET_WEIGHTS = [0.42, 0.24, 0.14, 0.08, 0.05, 0.04, 0.02, 0.01]

SHIPPING_METHODS = ["standard", "express", "next_day", "pickup"]
SHIPPING_WEIGHTS = [0.62, 0.22, 0.10, 0.06]


def load_parents() -> tuple[dict[str, date], list[str], dict[str, float]]:
    """Read customers and products so orders can reference real IDs."""
    customers = pd.read_csv(RAW_DIR / "customers.csv")
    products = pd.read_csv(RAW_DIR / "products.csv")

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


def build_orders(n, signup, product_ids, prices) -> tuple[list[dict], list[dict]]:
    """Build n order headers and their line items.

    order_total on the header is the sum of its line items. Storing it
    redundantly is deliberate: it lets a downstream test check that the
    header agrees with its lines, and inject_defects breaks that agreement
    on a fraction of orders.
    """
    customer_ids = list(signup)
    orders = []
    items = []
    item_seq = 1

    for i in range(1, n + 1):
        order_id = f"ORD-{i:07d}"
        cid = random.choice(customer_ids)
        order_ts = random_datetime_after(signup[cid])
        basket = random.choices(BASKET_SIZES, weights=BASKET_WEIGHTS)[0]

        # Sample without replacement so a normal order has distinct products.
        # The duplicate-product defect is injected separately, on purpose.
        chosen = random.sample(product_ids, min(basket, len(product_ids)))

        order_lines = []
        for line_no, pid in enumerate(chosen, start=1):
            qty = random.choices([1, 2, 3, 4, 5], weights=[0.55, 0.25, 0.10, 0.06, 0.04])[0]
            unit_price = round(prices[pid], 2)
            order_lines.append(
                {
                    "order_item_id": f"ITEM-{item_seq:08d}",
                    "order_id": order_id,
                    "line_number": line_no,
                    "product_id": pid,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "line_total": round(unit_price * qty, 2),
                }
            )
            item_seq += 1

        items.extend(order_lines)

        orders.append(
            {
                "order_id": order_id,
                "customer_id": cid,
                "order_ts": order_ts.isoformat(sep=" "),
                "status": random.choices(ORDER_STATUSES, weights=STATUS_WEIGHTS)[0],
                "item_count": len(order_lines),
                "order_total": round(sum(line["line_total"] for line in order_lines), 2),
                "shipping_method": random.choices(SHIPPING_METHODS, weights=SHIPPING_WEIGHTS)[0],
                "shipping_cost": round(random.uniform(0.0, 24.99), 2),
            }
        )

    return orders, items


def inject_defects(orders, items, signup, product_ids) -> tuple[list[dict], list[dict]]:
    """Break referential integrity and business rules on a fraction of rows."""
    n = len(orders)
    n_items = len(items)
    r = DEFECT_RATES_ORDERS

    # Orphan customer on the header.
    for idx in random.sample(range(n), int(n * r["orphan_customer"])):
        orders[idx]["customer_id"] = f"CUST-{random.randint(900000, 999999):06d}"

    # Orphan product on a line item.
    for idx in random.sample(range(n_items), int(n_items * r["orphan_product"])):
        items[idx]["product_id"] = f"PROD-{random.randint(90000, 99999):05d}"

    # Order dated before its customer signed up.
    for idx in random.sample(range(n), int(n * r["order_before_signup"])):
        cid = orders[idx]["customer_id"]
        if cid in signup:
            earlier = signup[cid] - timedelta(days=random.randint(1, 400))
            # Clamp to the business timeline. The order still predates its
            # customer's signup — that violation is the point — but it must
            # not fall outside the window the warehouse claims to cover, or
            # every time-series query grows a tail of near-empty months.
            earlier = max(earlier, START_DATE)
            orders[idx]["order_ts"] = datetime.combine(
                earlier, datetime.min.time()
            ).isoformat(sep=" ")

    # Absurd line quantities.
    for idx in random.sample(range(n_items), int(n_items * r["extreme_quantity"])):
        items[idx]["quantity"] = random.randint(500, 9999)
        items[idx]["line_total"] = round(
            items[idx]["unit_price"] * items[idx]["quantity"], 2
        )

    # Null quantity, with line_total left intact so the two disagree.
    for idx in random.sample(range(n_items), int(n_items * r["null_quantity"])):
        items[idx]["quantity"] = None

    # Header total disagreeing with the sum of its lines. No single-table
    # check finds this — it only shows up when you aggregate the children
    # and compare against the parent.
    for idx in random.sample(range(n), int(n * r["total_mismatch"])):
        orders[idx]["order_total"] = round(
            orders[idx]["order_total"] * random.uniform(0.55, 1.45), 2
        )

    # The same product twice on one order. Breaks any assumption that
    # (order_id, product_id) is a unique key — which an LLM will assume.
    dupe_lines = []
    next_seq = n_items + 1
    for idx in random.sample(range(n_items), int(n_items * r["duplicate_product_line"])):
        clone = dict(items[idx])
        clone["order_item_id"] = f"ITEM-{next_seq:08d}"
        next_seq += 1
        clone["line_number"] = clone["line_number"] + 100
        dupe_lines.append(clone)
    items = items + dupe_lines

    # Line items pointing at an order that does not exist.
    orphan_lines = []
    for idx in random.sample(range(len(items)), int(len(items) * r["orphan_order_line"])):
        clone = dict(items[idx])
        clone["order_item_id"] = f"ITEM-{next_seq:08d}"
        next_seq += 1
        clone["order_id"] = f"ORD-{random.randint(9_000_000, 9_999_999):07d}"
        orphan_lines.append(clone)
    items = items + orphan_lines

    # Order headers with no line items at all: a real integration failure,
    # where the header was written and the lines never arrived.
    empty_ids = {
        orders[idx]["order_id"]
        for idx in random.sample(range(n), int(n * r["order_without_items"]))
    }
    items = [line for line in items if line["order_id"] not in empty_ids]

    # Duplicate order_id on the header.
    dupes = [
        dict(orders[i])
        for i in random.sample(range(n), int(n * r["duplicate_order_id"]))
    ]
    orders = orders + dupes

    return orders, items


def main() -> None:
    signup, product_ids, prices = load_parents()

    orders, items = build_orders(N_ORDERS, signup, product_ids, prices)
    orders, items = inject_defects(orders, items, signup, product_ids)

    random.shuffle(orders)
    random.shuffle(items)

    orders_frame = pd.DataFrame(orders)
    items_frame = pd.DataFrame(items)

    orders_path = RAW_DIR / "orders.csv"
    items_path = RAW_DIR / "order_items.csv"
    orders_frame.to_csv(orders_path, index=False)
    items_frame.to_csv(items_path, index=False)

    valid_customers = set(signup)
    valid_products = set(product_ids)
    order_ids = set(orders_frame["order_id"])
    ids_with_items = set(items_frame["order_id"])

    print(f"wrote {len(orders_frame):,} orders to {orders_path}")
    print(f"  orphan customer_ids:  {(~orders_frame['customer_id'].isin(valid_customers)).sum():,}")
    print(f"  duplicate order_ids:  {orders_frame['order_id'].duplicated().sum():,}")
    print(f"  orders with no items: {len(order_ids - ids_with_items):,}")
    print(f"  avg items per order:  {orders_frame['item_count'].mean():.2f}")
    print("  status mix:")
    for name, count in orders_frame["status"].value_counts().items():
        print(f"    {name:<14} {count:>7,}")

    print(f"\nwrote {len(items_frame):,} line items to {items_path}")
    print(f"  orphan product_ids:   {(~items_frame['product_id'].isin(valid_products)).sum():,}")
    print(f"  orphan order_ids:     {(~items_frame['order_id'].isin(order_ids)).sum():,}")
    print(f"  null quantities:      {items_frame['quantity'].isna().sum():,}")
    print(f"  quantity > 100:       {(items_frame['quantity'] > 100).sum():,}")
    print(f"  duplicate (order, product) pairs: "
          f"{items_frame.duplicated(subset=['order_id', 'product_id']).sum():,}")


if __name__ == "__main__":
    main()