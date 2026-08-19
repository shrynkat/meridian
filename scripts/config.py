"""Shared configuration for Meridian's synthetic data generators."""

from datetime import date
from pathlib import Path

# Reproducibility. Every generator seeds from this single value, so the
# whole dataset is identical on every run and on every machine.
SEED = 42

# Paths. __file__ is this file; .parent is scripts/, .parent.parent is the
# project root. Deriving paths this way means the scripts work no matter
# which directory you run them from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"

# Dataset scale (medium).
N_CUSTOMERS = 10_000
N_PRODUCTS = 500
N_ORDERS = 100_000
N_EVENTS = 500_000
N_REVIEWS = 15_000

# The business timeline all dates fall within.
START_DATE = date(2023, 1, 1)
END_DATE = date(2026, 6, 30)

# Defect rates, as a fraction of rows affected. These are deliberate.
# Every entry here is something a dbt test in phase 4 will be written to catch.
DEFECT_RATES = {
    "customer_duplicate": 0.02,
    "customer_missing_email": 0.03,
    "customer_missing_city": 0.05,
    "customer_date_format": 0.10,
    "customer_segment_casing": 0.08,
}


# Product catalog settings.
CATEGORIES = {
    "Electronics": (29.99, 1499.99),
    "Home & Kitchen": (12.99, 399.99),
    "Apparel": (9.99, 199.99),
    "Sports & Outdoors": (14.99, 599.99),
    "Books": (6.99, 59.99),
    "Beauty": (7.99, 149.99),
}

DEFECT_RATES_PRODUCTS = {
    "missing_category": 0.03,
    "negative_price": 0.01,
    "cost_exceeds_price": 0.02,
    "category_whitespace": 0.06,
}


# Order settings.
ORDER_STATUSES = ["delivered", "shipped", "processing", "cancelled", "returned"]
STATUS_WEIGHTS = [0.70, 0.10, 0.05, 0.10, 0.05]

DEFECT_RATES_ORDERS = {
    "orphan_customer": 0.005,
    "orphan_product": 0.003,
    "order_before_signup": 0.01,
    "extreme_quantity": 0.002,
    "duplicate_order_id": 0.001,
    "null_quantity": 0.01,
}


# Clickstream event settings.
EVENT_TYPES = ["page_view", "product_view", "add_to_cart", "remove_from_cart", "checkout_start", "search"]
EVENT_WEIGHTS = [0.40, 0.25, 0.15, 0.05, 0.08, 0.07]
DEVICE_TYPES = ["mobile", "desktop", "tablet"]
DEVICE_WEIGHTS = [0.58, 0.35, 0.07]

DEFECT_RATES_EVENTS = {
    "null_session": 0.01,
    "orphan_customer": 0.02,
    "missing_properties": 0.03,
    "malformed_timestamp": 0.005,
}


# Review settings.
DEFECT_RATES_REVIEWS = {
    "empty_text": 0.04,
    "rating_out_of_range": 0.01,
    "sentiment_mismatch": 0.05,
    "unverified_purchase": 0.08,
    "duplicate_text": 0.02,
}
