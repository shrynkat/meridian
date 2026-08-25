-- Silver: orders cleaned, deduplicated, and conformed.
--
-- Rejection policy:
--   quarantined  orphan customer_id, orphan product_id, null quantity
--                (rows that break attribution or arithmetic)
--   flagged      extreme quantity, order predating customer signup
--                (rows that are improbable but real)
--   deduplicated duplicate order_id (identical copies, keep one)
--
-- quarantine/rejected_orders.sql selects the inverse of the exclusions
-- below, so every source row lands in exactly one of the two models.

with source as (

    select * from {{ source('bronze', 'orders') }}

),

customers as (

    select customer_id, signup_date
    from {{ ref('stg_customers') }}

),

products as (

    select product_id from {{ ref('stg_products') }}

),

typed as (

    select
        trim(order_id)                          as order_id,
        trim(customer_id)                       as customer_id,
        trim(product_id)                        as product_id,
        try_cast(quantity as integer)           as quantity,
        try_cast(unit_price as decimal(12, 2))  as unit_price,
        try_cast(order_total as decimal(14, 2)) as order_total,
        try_cast(order_ts as timestamp)         as order_ts,
        lower(trim(status))                     as status,
        _loaded_at,
        _source_file
    from source

),

deduplicated as (

    -- Duplicate order_ids are byte-identical copies of the same order.
    -- row_number over the id keeps exactly one. Ordering by _loaded_at
    -- makes the choice deterministic rather than arbitrary.
    select *
    from (
        select
            *,
            row_number() over (
                partition by order_id
                order by _loaded_at, order_ts
            ) as _row_num
        from typed
    )
    where _row_num = 1

),

validated as (

    select
        o.order_id,
        o.customer_id,
        o.product_id,
        o.quantity,
        o.unit_price,
        o.order_total,
        o.order_ts,
        o.status,

        -- Derived: recompute the total so downstream models can compare
        -- it against the source value rather than trusting either blindly.
        round(o.unit_price * o.quantity, 2) as computed_total,

        -- Flags: kept, not rejected.
        coalesce(o.quantity > 100, false)             as is_outlier_quantity,
        coalesce(o.order_ts::date < c.signup_date, false) as is_before_signup,

        -- Validity components, used here and inverted in quarantine.
        c.customer_id is not null as has_valid_customer,
        p.product_id is not null  as has_valid_product,

        o._loaded_at,
        o._source_file

    from deduplicated o
    left join customers c on o.customer_id = c.customer_id
    left join products  p on o.product_id  = p.product_id

)

select
    order_id,
    customer_id,
    product_id,
    quantity,
    unit_price,
    order_total,
    computed_total,
    order_ts,
    status,
    is_outlier_quantity,
    is_before_signup,
    _loaded_at,
    _source_file
from validated
where has_valid_customer
  and has_valid_product
  and quantity is not null
