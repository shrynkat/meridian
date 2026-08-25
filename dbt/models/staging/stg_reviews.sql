-- Silver: customer reviews cleaned and conformed.
--
-- Rejection policy:
--   quarantined  rating outside 1-5 (an invalid rating makes the row
--                unusable for any rating-based metric)
--   flagged      unverified purchase, empty review text
--
-- NOT HANDLED HERE, deliberately: sentiment-rating mismatch. A 5-star
-- rating attached to "Broke after a week. Very disappointed." passes
-- every constraint SQL can express — the rating is valid, the text is
-- non-empty, the foreign keys resolve. Detecting it requires reading the
-- text. That is what the LLM in phase 6 is for, and this gap is the
-- reason the project needs one.

with source as (

    select * from {{ source('bronze', 'reviews') }}

),

typed as (

    select
        trim(review_id)                     as review_id,
        trim(customer_id)                   as customer_id,
        trim(product_id)                    as product_id,
        try_cast(rating as integer)         as rating,

        -- Review text is free text: it carries commas, quotes, newlines,
        -- and emoji. Trim whitespace but otherwise leave it intact — any
        -- further normalisation would destroy signal the LLM needs.
        nullif(trim(review_text), '')       as review_text,

        try_cast(review_date as date)       as review_date,
        try_cast(verified_purchase as boolean) as verified_purchase,
        try_cast(helpful_votes as integer)  as helpful_votes,
        _loaded_at,
        _source_file

    from source

),

flagged as (

    select
        *,
        review_text is null as is_empty_review,
        length(review_text) as review_length,
        case
            when rating >= 4 then 'positive'
            when rating = 3  then 'neutral'
            else 'negative'
        end as rating_sentiment
    from typed

)

select
    review_id,
    customer_id,
    product_id,
    rating,
    rating_sentiment,
    review_text,
    review_length,
    review_date,
    verified_purchase,
    helpful_votes,
    is_empty_review,
    _loaded_at,
    _source_file
from flagged
where rating between 1 and 5
