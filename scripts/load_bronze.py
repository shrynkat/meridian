"""Load raw source files into the bronze layer of the Meridian warehouse.

Bronze is a faithful record of what the source systems sent. Nothing is
cleaned, cast, deduplicated, or filtered here. Every column lands as
VARCHAR (or JSON) so that malformed values survive the load intact rather
than being silently coerced or rejected by type inference.

Cleaning happens in silver, in dbt, where it is visible and testable.
"""

import duckdb

from config import PROJECT_ROOT, RAW_DIR

DB_PATH = PROJECT_ROOT / "meridian.duckdb"


def load_csv(con: duckdb.DuckDBPyConnection, table: str, filename: str) -> None:
    """Load one CSV into bronze with every column typed as VARCHAR.

    all_varchar=true disables type inference entirely. This is deliberate:
    inference samples rows, and our data contains columns that look like
    one type in the sample and another later (signup_date is ISO in most
    rows and US-format in others). Landing everything as text means no
    value is lost at the door.
    """
    source = RAW_DIR / filename
    con.execute(f"DROP TABLE IF EXISTS bronze.{table}")
    con.execute(
        f"""
        CREATE TABLE bronze.{table} AS
        SELECT
            *,
            now() AS _loaded_at,
            '{filename}' AS _source_file
        FROM read_csv(
            '{source}',
            all_varchar = true,
            header = true,
            sample_size = -1
        )
        """
    )


def load_jsonl(con: duckdb.DuckDBPyConnection, table: str, filename: str) -> None:
    """Load JSONL into bronze, keeping the nested properties object as JSON.

    The properties field has a different shape per event type, so it stays
    as a JSON column rather than being flattened. Phase 3 extracts from it
    with SQL.
    """
    source = RAW_DIR / filename
    con.execute(f"DROP TABLE IF EXISTS bronze.{table}")
    con.execute(
        f"""
        CREATE TABLE bronze.{table} AS
        SELECT
            event_id::VARCHAR      AS event_id,
            session_id::VARCHAR    AS session_id,
            customer_id::VARCHAR   AS customer_id,
            event_type::VARCHAR    AS event_type,
            event_ts::VARCHAR      AS event_ts,
            device_type::VARCHAR   AS device_type,
            properties::JSON       AS properties,
            now()                  AS _loaded_at,
            '{filename}'           AS _source_file
        FROM read_json(
            '{source}',
            format = 'newline_delimited',
            records = true
        )
        """
    )


def summarize(con: duckdb.DuckDBPyConnection) -> None:
    """Print row counts and confirm the defects survived the load."""
    tables = ["customers", "products", "orders", "events", "reviews"]

    print("\nrow counts")
    for table in tables:
        count = con.execute(f"SELECT count(*) FROM bronze.{table}").fetchone()[0]
        print(f"  bronze.{table:<12} {count:>9,}")

    print("\ndefects preserved in bronze")

    checks = [
        (
            "null emails",
            "SELECT count(*) FROM bronze.customers WHERE email IS NULL",
        ),
        (
            "N/A cities",
            "SELECT count(*) FROM bronze.customers WHERE city = 'N/A'",
        ),
        (
            "non-ISO signup dates",
            "SELECT count(*) FROM bronze.customers WHERE signup_date LIKE '%/%'",
        ),
        (
            "negative prices",
            "SELECT count(*) FROM bronze.products WHERE CAST(price AS DOUBLE) < 0",
        ),
        (
            "padded categories",
            "SELECT count(*) FROM bronze.products WHERE category LIKE '  %'",
        ),
        (
            "duplicate order_ids",
            """SELECT count(*) FROM (
                   SELECT order_id FROM bronze.orders
                   GROUP BY order_id HAVING count(*) > 1
               )""",
        ),
        (
            "null quantities",
            "SELECT count(*) FROM bronze.orders WHERE quantity IS NULL",
        ),
        (
            "orphan order customers",
            """SELECT count(*) FROM bronze.orders o
               LEFT JOIN bronze.customers c USING (customer_id)
               WHERE c.customer_id IS NULL""",
        ),
        (
            "anonymous events",
            "SELECT count(*) FROM bronze.events WHERE customer_id IS NULL",
        ),
        (
            "ratings out of range",
            """SELECT count(*) FROM bronze.reviews
               WHERE CAST(rating AS INTEGER) NOT BETWEEN 1 AND 5""",
        ),
    ]

    for label, sql in checks:
        result = con.execute(sql).fetchone()[0]
        print(f"  {label:<24} {result:>9,}")


def main() -> None:
    con = duckdb.connect(str(DB_PATH))
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze")

    load_csv(con, "customers", "customers.csv")
    load_csv(con, "products", "products.csv")
    load_csv(con, "orders", "orders.csv")
    load_csv(con, "reviews", "reviews.csv")
    load_jsonl(con, "events", "events.jsonl")

    summarize(con)

    size_mb = DB_PATH.stat().st_size / 1_048_576
    print(f"\nwarehouse written to {DB_PATH} ({size_mb:.1f} MB)")

    con.close()


if __name__ == "__main__":
    main()
