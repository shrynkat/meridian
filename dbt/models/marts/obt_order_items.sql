-- Gold: one big table. Line items pre-joined to order, customer, and
-- product attributes.
--
-- GRAIN: one row per order_item_id — same as fct_order_items.
--
-- Why this exists: the star schema (fct_order_items + dim_customers +
-- dim_products) is the correct model for a warehouse, but every join is a
-- chance for a text-to-SQL agent to pick the wrong key, the wrong grain, or
-- fan out a row count. This table answers most questions with zero joins.
--
-- The cost is storage and staleness — it must be rebuilt whenever its
-- inputs change, which dbt handles via the dependency graph. At this data
-- volume that cost is nil. At a billion rows it would not be.
--
-- Phase 8 runs the same benchmark questions against both this table and
-- the star schema, so "does denormalisation improve text-to-SQL accuracy"
-- becomes a measured result rather than an assumption.
--
-- CAUTION on aggregation: order-level measures (order_total, shipping_cost)
-- are REPEATED on every line of the same order. Summing them here
-- double-counts. Use line_total for revenue at this grain, or query
-- fct_orders for order-level totals.

with items as (

    select * from {{ ref('fct_order_items') }}

),

orders as (

    select
        order_id,
        order_total,
        shipping_cost,
        shipping_method
    from {{ ref('fct_orders') }}

),

customers as (

    select
        customer_id,
        first_name,
        last_name,
        email,
        city,
        state,
        segment,
        signup_date,
        is_repeat_customer
    from {{ ref('dim_customers') }}

),

products as (

    select
        product_id,
        product_name,
        category,
        price as list_price,
        cost,
        gross_margin,
        avg_rating,
        review_count
    from {{ ref('dim_products') }}

)

select
    -- Line item
    i.order_item_id,
    i.line_number,
    i.quantity,
    i.unit_price,
    i.line_total,
    i.line_total_ex_outliers,

    -- Order context (REPEATED per line — do not sum these here)
    i.order_id,
    i.order_ts,
    i.order_date,
    i.order_month,
    i.order_year,
    i.status,
    i.is_completed,
    i.order_item_count,
    o.order_total,
    o.shipping_cost,
    o.shipping_method,

    -- Customer
    i.customer_id,
    c.first_name,
    c.last_name,
    c.email,
    c.city as customer_city,
    c.state as customer_state,
    c.segment as customer_segment,
    c.signup_date as customer_signup_date,
    c.is_repeat_customer,

    -- Product
    i.product_id,
    p.product_name,
    p.category as product_category,
    p.list_price,
    p.cost as product_cost,
    p.gross_margin as product_margin,
    p.avg_rating as product_avg_rating,
    p.review_count as product_review_count,

    -- Derived
    round(i.quantity * p.gross_margin, 2) as est_line_profit,

    -- Quality flags
    i.is_outlier_quantity,
    i.is_duplicate_product_line,
    i.is_line_total_mismatched

from items i
left join orders    o on i.order_id    = o.order_id
left join customers c on i.customer_id = c.customer_id
left join products  p on i.product_id  = p.product_id
