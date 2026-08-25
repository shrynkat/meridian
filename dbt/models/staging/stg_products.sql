-- Silver: product catalog cleaned and conformed.
--
-- Rejection policy:
--   quarantined  negative price (a price below zero is unusable)
--   flagged      cost exceeding price (a margin violation worth surfacing,
--                but the product and its price are still real)
--   cleaned      padded category strings, null categories

with source as (

    select * from {{ source('bronze', 'products') }}

),

typed as (

    select
        trim(product_id)                       as product_id,
        trim(product_name)                     as product_name,

        -- '  Books  ' and 'Books' are the same category. Without the trim
        -- a group-by splits one category into two.
        nullif(trim(category), '')             as category,

        try_cast(price as decimal(12, 2))      as price,
        try_cast(cost as decimal(12, 2))       as cost,
        try_cast(in_stock as boolean)          as in_stock,
        _loaded_at,
        _source_file

    from source

),

flagged as (

    select
        *,
        -- Cost above price: every field is a valid number and the row is
        -- still wrong. Only domain knowledge catches this.
        coalesce(cost > price, false) as is_margin_violation,
        coalesce(category is null, true) as is_uncategorized
    from typed

)

select
    product_id,
    product_name,
    category,
    price,
    cost,
    round(price - cost, 2) as gross_margin,
    in_stock,
    is_margin_violation,
    is_uncategorized,
    _loaded_at,
    _source_file
from flagged
where price is not null
  and price >= 0
