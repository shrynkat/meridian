-- Gold: order fact. One row per order.
--
-- GRAIN: one row per order_id.
--
-- This is the table for counting orders and summing order revenue.
-- Summing order_total here is correct. Summing order_total after joining
-- to fct_order_items is NOT — it multiplies each order's total by its
-- line count.

with orders as (

    select * from {{ ref('stg_orders') }}

)

select
    order_id,
    customer_id,

    order_ts,
    order_date,
    date_trunc('month', order_date)::date as order_month,
    year(order_date)                      as order_year,
    dayname(order_date)                   as order_day_of_week,

    status,
    status not in ('cancelled', 'returned') as is_completed,

    item_count,
    actual_line_count,
    order_total,
    actual_line_total,
    shipping_cost,
    round(order_total + shipping_cost, 2) as order_total_with_shipping,

    shipping_method,

    -- Data quality flags carried forward from silver so consumers can
    -- filter on them rather than rediscovering the problems.
    is_before_signup,
    has_no_source_items,
    all_items_rejected,
    is_total_mismatched

from orders
