-- Line items excluded from silver, each with the reasons it was rejected.
--
-- Inverse of stg_order_items' where clause, so:
--   count(stg_order_items) + count(rejected_order_items)
--     = count(bronze.order_items)
--
-- A line can fail more than one rule; rejection_reason records all of them.

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
        trim(order_item_id)                    as order_item_id,
        trim(order_id)                         as order_id,
        try_cast(line_number as integer)       as line_number,
        trim(product_id)                       as product_id,
        try_cast(quantity as integer)          as quantity,
        try_cast(unit_price as decimal(12, 2)) as unit_price,
        try_cast(line_total as decimal(14, 2)) as line_total,
        _loaded_at,
        _source_file
    from source

),

evaluated as (

    select
        i.*,
        o.order_id is null   as missing_order,
        p.product_id is null as missing_product,
        i.quantity is null   as missing_quantity
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

    array_to_string(
        list_filter(
            [
                case when missing_order    then 'orphan_order_id'   end,
                case when missing_product  then 'orphan_product_id' end,
                case when missing_quantity then 'null_quantity'     end
            ],
            x -> x is not null
        ),
        ', '
    ) as rejection_reason,

    current_timestamp as _quarantined_at,
    _source_file

from evaluated
where missing_order
   or missing_product
   or missing_quantity
