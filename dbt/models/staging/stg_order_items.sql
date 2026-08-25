-- Silver: order line items. One row per product on an order.
--
-- GRAIN: one row per order_item_id. An order appears here as many times
-- as it has products. This is the table for basket analysis and
-- product-level revenue; it is NOT the table for counting orders.
--
-- Note that (order_id, product_id) is NOT unique — the same product can
-- legitimately appear twice on one order. Only order_item_id is a key.
--
-- Rejection policy:
--   quarantined  orphan order_id, orphan product_id, null quantity
--   flagged      extreme quantity, duplicate product on the same order

with source as (

    select * from {{ source('bronze', 'order_items') }}

),

orders as (

    -- distinct is load-bearing: bronze.orders contains duplicate
    -- order_ids, and joining to it without deduplicating fans out
    -- every line item on a duplicated order.
    select distinct trim(order_id) as order_id
    from {{ source('bronze', 'orders') }}

),

products as (

    select product_id from {{ ref('stg_products') }}

),

typed as (

    select
        trim(order_item_id)                     as order_item_id,
        trim(order_id)                          as order_id,
        try_cast(line_number as integer)        as line_number,
        trim(product_id)                        as product_id,
        try_cast(quantity as integer)           as quantity,
        try_cast(unit_price as decimal(12, 2))  as unit_price,
        try_cast(line_total as decimal(14, 2))  as line_total,
        _loaded_at,
        _source_file
    from source

),

validated as (

    select
        i.*,

        -- Recomputed from the components, so downstream models can compare
        -- against the stored value rather than trusting either blindly.
        round(i.unit_price * i.quantity, 2) as computed_line_total,

        coalesce(i.quantity > 100, false) as is_outlier_quantity,

        count(*) over (
            partition by i.order_id, i.product_id
        ) > 1 as is_duplicate_product_line,

        o.order_id is not null as has_valid_order,
        p.product_id is not null as has_valid_product

    from typed i
    left join orders   o on i.order_id   = o.order_id
    left join products p on i.product_id = p.product_id

)

select
    order_item_id,
    order_id,
    line_number,
    product_id,
    quantity,
    unit_price,
    line_total,
    computed_line_total,
    is_outlier_quantity,
    is_duplicate_product_line,
    _loaded_at,
    _source_file
from validated
where has_valid_order
  and has_valid_product
  and quantity is not null
