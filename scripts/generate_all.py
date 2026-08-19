"""Run every Meridian data generator in dependency order."""

import random
import time

from faker import Faker

from config import SEED

import generate_customers
import generate_products
import generate_orders
import generate_events
import generate_reviews

# Order matters: orders reads customers and products; reviews reads all three.
STAGES = [
    ("customers", generate_customers),
    ("products", generate_products),
    ("orders", generate_orders),
    ("events", generate_events),
    ("reviews", generate_reviews),
]


def main() -> None:
    started = time.perf_counter()
    for name, module in STAGES:
        # Reseed before every stage. The generators seed at import time,
        # which means running them through this orchestrator would otherwise
        # leave each stage continuing from the previous stage's RNG state
        # instead of starting fresh. Row counts stay the same either way,
        # so the drift is invisible unless you compare defect counts.
        random.seed(SEED)
        Faker.seed(SEED)

        print(f"\n=== {name} ===")
        stage_start = time.perf_counter()
        module.main()
        print(f"  ({time.perf_counter() - stage_start:.1f}s)")
    print(f"\nall stages complete in {time.perf_counter() - started:.1f}s")


if __name__ == "__main__":
    main()
