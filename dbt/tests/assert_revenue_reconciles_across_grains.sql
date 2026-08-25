-- Revenue computed from the line-item fact must equal revenue computed
-- from the customer dimension. These are two independent aggregation
-- paths through the warehouse; if they disagree, one of them is wrong.
--
-- This test exists because dim_customers once reported NEGATIVE $520M
-- revenue: it subtracted outlier line totals from header totals that never
-- contained them. The bug was found by hand. This catches it automatically.
--
-- Tolerance is 1 cent to allow for decimal rounding across the two paths.

with from_lines as (
    select sum(line_total) as revenue
    from {{ ref('fct_order_items') }}
),

from_customers as (
    select sum(line_revenue) as revenue
    from {{ ref('dim_customers') }}
)

select
    l.revenue as revenue_from_line_items,
    c.revenue as revenue_from_dim_customers,
    abs(l.revenue - c.revenue) as difference
from from_lines l
cross join from_customers c
where abs(l.revenue - c.revenue) > 0.01
