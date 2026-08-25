-- Silver: order headers, cleaned and deduplicated. One row per order.
--
-- GRAIN: one row per order_id. Product and quantity now live on
-- stg_order_items. Summing order_total here gives total revenue;
-- summing it after joining to line items double-counts.
--
-- Rejection policy:
--   quarantined  orphan customer_id (breaks attribution)
--   flagged      order predating signup, total disagreeing with lines,
--                order with no line items
--   deduplicated duplicate order_id

with source as (

    select * from {{ source('bronze', 'orders') }}

),

customers as (

    select customer_id, signup_date
    from {{ ref('stg_customers') }}

),

typed as (

    select
        trim(order_id)                           as order_id,
        trim(customer_id)                        as customer_id,
        try_cast(order_ts as timestamp)          as order_ts,
        lower(trim(status))                      as status,
        try_cast(item_count as integer)          as item_count,
        try_cast(order_total as decimal(14, 2))  as order_total,
        lower(trim(shipping_method))             as shipping_method,
        try_cast(shipping_cost as decimal(8, 2)) as shipping_cost,
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

-- Aggregate the line items so the header can be reconciled against them.
-- This is the only way to catch a header total that disagrees with its
-- children: no single-table constraint can express it.
line_totals as (

    select
        order_id,
        count(*)        as actual_line_count,
        sum(line_total) as actual_line_total
    from {{ ref('stg_order_items') }}
    group by order_id

),

-- Lines as they arrived, before our own rejection rules ran. An order with
-- no rows here never had line items: an upstream integration failure. An
-- order with rows here but none in line_totals had all its lines
-- quarantined, which is a consequence of our rules, not the source's fault.
-- Conflating the two would misattribute the cause.
source_lines as (

    select distinct trim(order_id) as order_id
    from {{ source('bronze', 'order_items') }}

),

validated as (

    select
        o.order_id,
        o.customer_id,
        o.order_ts,
        o.order_ts::date as order_date,
        o.status,
        o.item_count,
        o.order_total,
        o.shipping_method,
        o.shipping_cost,

        coalesce(l.actual_line_count, 0) as actual_line_count,
        l.actual_line_total,

        -- Flags
        coalesce(o.order_ts::date < c.signup_date, false) as is_before_signup,
        s.order_id is null                                as has_no_source_items,
        (s.order_id is not null and l.order_id is null)   as all_items_rejected,
        coalesce(
            abs(o.order_total - l.actual_line_total) > 0.01,
            false
        ) as is_total_mismatched,

        c.customer_id is not null as has_valid_customer,

        o._loaded_at,
        o._source_file

    from deduplicated o
    left join customers   c on o.customer_id = c.customer_id
    left join line_totals  l on o.order_id   = l.order_id
    left join source_lines s on o.order_id   = s.order_id

)

select
    order_id,
    customer_id,
    order_ts,
    order_date,
    status,
    item_count,
    actual_line_count,
    order_total,
    actual_line_total,
    shipping_method,
    shipping_cost,
    is_before_signup,
    has_no_source_items,
    all_items_rejected,
    is_total_mismatched,
    _loaded_at,
    _source_file
from validated
where has_valid_customer
