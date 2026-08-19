"""Generate a synthetic product catalog with deliberate defects."""

import random

import pandas as pd
from faker import Faker

from config import SEED, RAW_DIR, N_PRODUCTS, CATEGORIES, DEFECT_RATES_PRODUCTS

fake = Faker("en_US")
Faker.seed(SEED)
random.seed(SEED)


def build_products(n: int) -> list[dict]:
    """Build n clean product records with category-appropriate pricing."""
    rows = []
    for i in range(1, n + 1):
        category = random.choice(list(CATEGORIES))
        low, high = CATEGORIES[category]
        price = round(random.uniform(low, high), 2)
        # Cost is 40-70% of price, giving a realistic gross margin.
        cost = round(price * random.uniform(0.40, 0.70), 2)
        rows.append(
            {
                "product_id": f"PROD-{i:05d}",
                "product_name": f"{fake.color_name()} {fake.word().capitalize()} {random.choice(['Pro', 'Plus', 'Max', 'Lite', 'Classic'])}",
                "category": category,
                "price": price,
                "cost": cost,
                "in_stock": random.choice([True, True, True, False]),
            }
        )
    return rows


def inject_defects(rows: list[dict]) -> None:
    """Corrupt a fraction of rows in place."""
    n = len(rows)

    for idx in random.sample(range(n), int(n * DEFECT_RATES_PRODUCTS["missing_category"])):
        rows[idx]["category"] = None

    # Negative prices: impossible, but source systems produce them.
    for idx in random.sample(range(n), int(n * DEFECT_RATES_PRODUCTS["negative_price"])):
        rows[idx]["price"] = -abs(rows[idx]["price"])

    # Cost above price: a margin violation, valid as a number but wrong as a fact.
    for idx in random.sample(range(n), int(n * DEFECT_RATES_PRODUCTS["cost_exceeds_price"])):
        rows[idx]["cost"] = round(abs(rows[idx]["price"]) * random.uniform(1.05, 1.40), 2)

    # Padded category strings: group-by traps.
    for idx in random.sample(range(n), int(n * DEFECT_RATES_PRODUCTS["category_whitespace"])):
        if rows[idx]["category"]:
            rows[idx]["category"] = f"  {rows[idx]['category']}  "


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    rows = build_products(N_PRODUCTS)
    inject_defects(rows)

    frame = pd.DataFrame(rows)
    out_path = RAW_DIR / "products.csv"
    frame.to_csv(out_path, index=False)

    print(f"wrote {len(frame):,} rows to {out_path}")
    print(f"  null categories:    {frame['category'].isna().sum():,}")
    print(f"  negative prices:    {(frame['price'] < 0).sum():,}")
    print(f"  cost > price:       {(frame['cost'] > frame['price'].abs()).sum():,}")
    print(f"  padded categories:  {frame['category'].str.startswith('  ', na=False).sum():,}")


if __name__ == "__main__":
    main()
