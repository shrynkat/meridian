-- Order headers whose stored total disagrees with the sum of their line
-- items. This is a business rule violation that no single-table constraint
-- can express: every field on the header is valid, every field on each
-- line is valid, and the relationship between them is wrong.
--
-- Warn rather than error: ~6,365 orders are expected to fail, because
-- defect injection modifies line quantities without recomputing headers.
-- The value of this test is the COUNT. A stable number is fine; a change
-- means something upstream moved.

{{ config(severity = 'warn') }}

select
    o.order_id,
    o.order_total          as header_total,
    o.actual_line_total    as sum_of_lines,
    round(abs(o.order_total - o.actual_line_total), 2) as discrepancy,
    o.actual_line_count
from {{ ref('stg_orders') }} o
where o.is_total_mismatched
