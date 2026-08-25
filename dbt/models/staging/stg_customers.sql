-- Silver: customers cleaned and conformed.
-- Rows failing hard validation are excluded here and captured in
-- quarantine/rejected_customers.sql using the inverse condition.

with source as (

    select * from {{ source('bronze', 'customers') }}

),

cleaned as (

    select
        customer_id,
        first_name,
        last_name,

        -- Emails: trim, lowercase, and treat blanks as null. This is what
        -- collapses the planted duplicates (uppercased + trailing space)
        -- onto their originals.
        nullif(lower(trim(email)), '') as email,

        -- Two date formats in one column. try_strptime returns null on a
        -- failed parse instead of erroring, so coalesce picks whichever
        -- format matched.
        coalesce(
            try_cast(signup_date as date),
            try_strptime(signup_date, '%m/%d/%Y')::date
        ) as signup_date,

        -- 'N/A' is a placeholder, not a real city. Convert to a true null
        -- so downstream null checks actually find it.
        nullif(nullif(trim(city), ''), 'N/A') as city,

        upper(trim(state)) as state,
        upper(trim(country)) as country,

        -- Business/business/Consumer/consumer collapse to two values.
        lower(trim(segment)) as segment,

        try_cast(marketing_opt_in as boolean) as marketing_opt_in,

        _loaded_at,
        _source_file

    from source

)

select * from cleaned
where signup_date is not null
