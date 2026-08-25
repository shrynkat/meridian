-- Order headers excluded from silver.
--
-- At the header grain the only hard rejection is an orphan customer_id:
-- an order that cannot be attributed to anyone corrupts every
-- customer-level metric.
--
-- Line-item rejections are captured separately in rejected_order_items.

with source as (

    select * from {{ source('bronze', 'orders') }}

),

customers as (

    select customer_id from {{ ref('stg_customers') }}

),

typed as (

    select
        trim(order_id)                          as order_id,
        trim(customer_id)                       as customer_id,
        try_cast(order_ts as timestamp)         as order_ts,
        lower(trim(status))                     as status,
        try_cast(order_total as decimal(14, 2)) as order_total,
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

)

select
    o.order_id,
    o.customer_id,
    o.order_ts,
    o.status,
    o.order_total,
    'orphan_customer_id' as rejection_reason,
    current_timestamp    as _quarantined_at,
    o._source_file
from deduplicated o
left join customers c on o.customer_id = c.customer_id
where c.customer_id is null
