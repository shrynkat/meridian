-- Gold: order line item fact. One row per product on an order.
--
-- GRAIN: one row per order_item_id.
--
-- This is the table for product-level revenue, units sold, and basket
-- analysis. Summing line_total here gives total revenue and agrees with
-- summing order_total from fct_orders (for orders whose totals reconcile).
--
-- (order_id, product_id) is NOT unique: the same product can appear twice
-- on one order. Only order_item_id is a key.

with items as (

    select * from {{ ref('stg_order_items') }}

),

orders as (

    select
        order_id,
        customer_id,
        order_ts,
        order_date,
        status,
        item_count
    from {{ ref('stg_orders') }}

)

select
    i.order_item_id,
    i.order_id,
    o.customer_id,
    i.product_id,

    i.line_number,
    i.quantity,
    i.unit_price,
    i.line_total,
    i.computed_line_total,

    -- line_total with outliers zeroed out, so SUM() over this column gives
    -- a revenue figure that a handful of thousand-unit lines cannot
    -- dominate. Use line_total when you want the raw figure including them.
    case when i.is_outlier_quantity then 0 else i.line_total end
        as line_total_ex_outliers,

    o.order_ts,
    o.order_date,
    date_trunc('month', o.order_date)::date as order_month,
    year(o.order_date)                      as order_year,

    o.status,
    o.status not in ('cancelled', 'returned') as is_completed,

    -- Basket context: how many lines the parent order had. Lets you answer
    -- "what sells in large baskets" without re-aggregating.
    o.item_count as order_item_count,

    i.is_outlier_quantity,
    i.is_duplicate_product_line,

    -- The stored line total disagreeing with quantity * unit_price is a
    -- row whose own arithmetic does not hold.
    coalesce(
        abs(i.line_total - i.computed_line_total) > 0.01,
        false
    ) as is_line_total_mismatched

from items i
join orders o on i.order_id = o.order_id
