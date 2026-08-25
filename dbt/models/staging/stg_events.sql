-- Silver: clickstream events, with nested JSON properties flattened.
--
-- The properties object carries different fields per event type, so this
-- model extracts every possible field into its own column. json_extract_string
-- returns null when the path does not exist, which means a page_view row has
-- page_url populated and product_id null, and a product_view row the reverse.
-- The result is one wide, sparse table — which is the correct shape for a
-- staging model, because it lets downstream marts filter by event_type and
-- select only the columns that apply.
--
-- Rejection policy:
--   quarantined  unparseable event_ts (an event with no valid time cannot
--                be placed in any time-based analysis)
--   flagged      null session_id, anonymous traffic, empty properties
--
-- Note on anonymous events: a null customer_id here is NOT an error. It is
-- a visitor who never logged in, which is a legitimate and expected fact
-- about clickstream data. Contrast with orders, where a missing customer
-- breaks attribution and gets quarantined. Same technical symptom,
-- opposite treatment, because the business meaning differs.

with source as (

    select * from {{ source('bronze', 'events') }}

),

typed as (

    select
        trim(event_id)          as event_id,
        nullif(trim(session_id), '') as session_id,
        nullif(trim(customer_id), '') as customer_id,
        lower(trim(event_type)) as event_type,
        lower(trim(device_type)) as device_type,

        -- Two timestamp formats: ISO strings for most rows, epoch-second
        -- integers for the malformed ones. try_cast returns null rather
        -- than erroring, so coalesce picks whichever branch parsed.
        coalesce(
            try_cast(event_ts as timestamp),
            case
                when event_ts similar to '[0-9]+'
                then to_timestamp(try_cast(event_ts as bigint))::timestamp
            end
        ) as event_ts,

        properties,
        _loaded_at,
        _source_file

    from source

),

extracted as (

    select
        event_id,
        session_id,
        customer_id,
        event_type,
        device_type,
        event_ts,

        -- page_view fields
        json_extract_string(properties, '$.page_url')  as page_url,
        json_extract_string(properties, '$.referrer')  as referrer,
        try_cast(json_extract_string(properties, '$.load_time_ms') as integer) as load_time_ms,

        -- product_view / cart fields
        json_extract_string(properties, '$.product_id') as product_id,
        try_cast(json_extract_string(properties, '$.scroll_depth_pct') as integer) as scroll_depth_pct,
        try_cast(json_extract_string(properties, '$.time_on_page_sec') as integer) as time_on_page_sec,
        try_cast(json_extract_string(properties, '$.quantity') as integer) as cart_quantity,

        -- checkout_start fields
        try_cast(json_extract_string(properties, '$.cart_size') as integer) as cart_size,
        try_cast(json_extract_string(properties, '$.cart_value') as decimal(12, 2)) as cart_value,
        json_extract_string(properties, '$.payment_method') as payment_method,

        -- search fields
        json_extract_string(properties, '$.query') as search_query,
        try_cast(json_extract_string(properties, '$.results_count') as integer) as results_count,

        -- Flags
        customer_id is null  as is_anonymous,
        session_id is null   as is_sessionless,
        properties::varchar in ('{}', 'null') as has_empty_properties,

        _loaded_at,
        _source_file

    from typed

)

select
    event_id,
    session_id,
    customer_id,
    event_type,
    device_type,
    event_ts,
    event_ts::date as event_date,
    page_url,
    referrer,
    load_time_ms,
    product_id,
    scroll_depth_pct,
    time_on_page_sec,
    cart_quantity,
    cart_size,
    cart_value,
    payment_method,
    search_query,
    results_count,
    is_anonymous,
    is_sessionless,
    has_empty_properties,
    _loaded_at,
    _source_file
from extracted
where event_ts is not null
