-- Customers excluded from silver, with the reason recorded.
-- The where clause here is the exact inverse of stg_customers'.

with source as (

    select * from {{ source('bronze', 'customers') }}

),

evaluated as (

    select
        customer_id,
        email,
        signup_date as raw_signup_date,
        city,
        segment,
        coalesce(
            try_cast(signup_date as date),
            try_strptime(signup_date, '%m/%d/%Y')::date
        ) as parsed_signup_date,
        _loaded_at,
        _source_file
    from source

)

select
    customer_id,
    email,
    raw_signup_date,
    city,
    segment,
    'unparseable_signup_date' as rejection_reason,
    current_timestamp as _quarantined_at,
    _source_file
from evaluated
where parsed_signup_date is null
