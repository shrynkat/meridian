"""Generate synthetic customer records with deliberate data-quality defects."""

import random
from datetime import date, timedelta

import pandas as pd
from faker import Faker

from config import (
    SEED,
    RAW_DIR,
    N_CUSTOMERS,
    START_DATE,
    END_DATE,
    DEFECT_RATES,
)

fake = Faker("en_US")
Faker.seed(SEED)
random.seed(SEED)

SEGMENTS = ["consumer", "business"]


def random_date(start: date, end: date) -> date:
    """Pick a uniformly random date between two bounds, inclusive."""
    span = (end - start).days
    return start + timedelta(days=random.randint(0, span))


def build_customers(n: int) -> list[dict]:
    """Build n clean customer records."""
    rows = []
    for i in range(1, n + 1):
        first = fake.first_name()
        last = fake.last_name()
        rows.append(
            {
                "customer_id": f"CUST-{i:06d}",
                "first_name": first,
                "last_name": last,
                "email": f"{first.lower()}.{last.lower()}{random.randint(1, 999)}@{fake.free_email_domain()}",
                "signup_date": random_date(START_DATE, END_DATE).isoformat(),
                "city": fake.city(),
                "state": fake.state_abbr(),
                "country": "US",
                "segment": random.choice(SEGMENTS),
                "marketing_opt_in": random.choice([True, False]),
            }
        )
    return rows


def inject_defects(rows: list[dict]) -> None:
    """Corrupt a fraction of rows in place. Each defect is one dbt test later."""
    n = len(rows)

    # Null emails: the field is simply absent.
    for idx in random.sample(range(n), int(n * DEFECT_RATES["customer_missing_email"])):
        rows[idx]["email"] = None

    # Empty-string cities: "missing" represented a second, different way.
    # Real source systems are rarely consistent about which one they use.
    for idx in random.sample(range(n), int(n * DEFECT_RATES["customer_missing_city"])):
        rows[idx]["city"] = ""

    # Mixed date formats: ISO for most rows, US-style for some.
    for idx in random.sample(range(n), int(n * DEFECT_RATES["customer_date_format"])):
        parsed = date.fromisoformat(rows[idx]["signup_date"])
        rows[idx]["signup_date"] = parsed.strftime("%m/%d/%Y")

    # Inconsistent casing on a categorical field.
    for idx in random.sample(range(n), int(n * DEFECT_RATES["customer_segment_casing"])):
        rows[idx]["segment"] = rows[idx]["segment"].capitalize()


def add_duplicates(rows: list[dict]) -> list[dict]:
    """Append near-duplicate records: same person, new ID, altered email."""
    k = int(len(rows) * DEFECT_RATES["customer_duplicate"])
    next_id = len(rows) + 1
    dupes = []
    for idx in random.sample(range(len(rows)), k):
        dupe = dict(rows[idx])
        dupe["customer_id"] = f"CUST-{next_id:06d}"
        next_id += 1
        if dupe["email"]:
            dupe["email"] = dupe["email"].upper() + " "
        dupes.append(dupe)
    return rows + dupes


def main() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    rows = build_customers(N_CUSTOMERS)
    inject_defects(rows)
    rows = add_duplicates(rows)
    random.shuffle(rows)

    frame = pd.DataFrame(rows)
    out_path = RAW_DIR / "customers.csv"
    frame.to_csv(out_path, index=False)

    print(f"wrote {len(frame):,} rows to {out_path}")
    print(f"  null emails:     {frame['email'].isna().sum():,}")
    print(f"  empty cities:    {(frame['city'] == '').sum():,}")
    print(f"  non-ISO dates:   {frame['signup_date'].str.contains('/').sum():,}")
    print(f"  segment values:  {sorted(frame['segment'].unique())}")


if __name__ == "__main__":
    main()
