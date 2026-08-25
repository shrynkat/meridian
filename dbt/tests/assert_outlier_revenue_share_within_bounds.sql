-- Guards against outlier line quantities dominating revenue further than
-- they already do.
--
-- Context: 452 line items (0.2% of rows) carry ~85% of gross revenue,
-- because defect injection sets their quantities to 500-9999 units. That
-- is known and deliberate. This test does not assert the share is healthy
-- — it asserts the share has not MOVED, which would mean the generator,
-- the outlier threshold, or the rejection rules changed without anyone
-- noticing.
--
-- The 90% ceiling is a regression guard, not a quality bar.

with revenue_split as (
    select
        sum(line_total)                                          as total_revenue,
        sum(case when is_outlier_quantity then line_total else 0 end) as outlier_revenue,
        count(*)                                                 as total_lines,
        count(*) filter (where is_outlier_quantity)              as outlier_lines
    from {{ ref('fct_order_items') }}
)

select
    total_lines,
    outlier_lines,
    round(outlier_lines::double / total_lines * 100, 3) as outlier_row_pct,
    round(outlier_revenue / total_revenue * 100, 2)     as outlier_revenue_pct
from revenue_split
where outlier_revenue / total_revenue > 0.90
