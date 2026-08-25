-- No order or event may be dated in the future.
--
-- Cheap, and it catches a broad class of ingestion faults: timezone
-- mishandling, epoch-vs-millisecond confusion, a test fixture leaking into
-- production data, or a source system with a wrong clock.

with future_orders as (
    select
        'fct_orders'  as source_table,
        order_id      as record_id,
        order_ts::timestamp as bad_timestamp
    from {{ ref('fct_orders') }}
    where order_ts > current_timestamp
),

future_events as (
    select
        'stg_events'  as source_table,
        event_id      as record_id,
        event_ts::timestamp as bad_timestamp
    from {{ ref('stg_events') }}
    where event_ts > current_timestamp
)

select * from future_orders
union all
select * from future_events
