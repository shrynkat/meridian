-- Orders excluded from silver, each with the reason it was rejected.
--
-- This is the inverse of stg_orders' where clause. Every bronze order
-- lands in exactly one of the two models, so:
--   count(stg_orders) + count(rejected_orders) + duplicates_removed
--     = count(bronze.orders)
--
-- A row can fail more than one rule. rejection_reason records all of
-- them rather than only the first, so the counts by reason will sum to
-- more than the row count.

with source as (

    select * from {{ source('bronze', 'orders') }}

),

customers as (

    select customer_id from {{ ref('stg_customers') }}

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

evaluated as (

    select
        o.*,
        c.customer_id is null as missing_customer,
        p.product_id is null  as missing_product,
        o.quantity is null    as missing_quantity
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
    order_ts,
    status,

    -- All failing rules, comma-separated. list_filter drops the nulls
    -- left by the case expressions that did not fire.
    array_to_string(
        list_filter(
            [
                case when missing_customer then 'orphan_customer_id' end,
                case when missing_product  then 'orphan_product_id'  end,
                case when missing_quantity then 'null_quantity'      end
            ],
            x -> x is not null
        ),
        ', '
    ) as rejection_reason,

    current_timestamp as _quarantined_at,
    _source_file

from evaluated
where missing_customer
   or missing_product
   or missing_quantity
