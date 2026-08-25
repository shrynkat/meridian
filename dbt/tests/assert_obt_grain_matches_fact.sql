-- The one-big-table must have exactly one row per line item.
--
-- obt_order_items joins fct_order_items to three other tables. If any of
-- those join keys is not unique, the join fans out and every aggregate
-- computed from the OBT is silently inflated. Nothing errors; the row
-- count just grows.
--
-- This is the exact bug that hit stg_order_items: joining to bronze.orders
-- without deduplicating multiplied 218 rows. Row counts are the cheapest
-- possible detector for it.

with counts as (
    select
        (select count(*) from {{ ref('fct_order_items') }}) as fact_rows,
        (select count(*) from {{ ref('obt_order_items') }}) as obt_rows,
        (select count(distinct order_item_id) from {{ ref('obt_order_items') }}) as obt_distinct_keys
)

select
    fact_rows,
    obt_rows,
    obt_distinct_keys,
    obt_rows - fact_rows as row_difference
from counts
where obt_rows != fact_rows
   or obt_rows != obt_distinct_keys
