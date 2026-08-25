-- Gold: product dimension. One row per product.
--
-- GRAIN: one row per product_id.
--
-- Sales aggregates come from the line-item grain, since that is where
-- product-level quantities and revenue live. Review aggregates come from
-- stg_reviews.

with products as (

    select * from {{ ref('stg_products') }}

),

items as (

    select
        i.product_id,
        i.quantity,
        i.line_total,
        i.is_outlier_quantity,
        o.status,
        o.order_id
    from {{ ref('stg_order_items') }} i
    join {{ ref('stg_orders') }} o on i.order_id = o.order_id

),

sales_stats as (

    select
        product_id,

        count(distinct order_id)                     as orders_containing,
        sum(quantity)                                as units_sold,
        sum(quantity) filter (
            where status not in ('cancelled', 'returned')
        )                                            as net_units_sold,

        sum(line_total)                              as gross_revenue,
        sum(line_total) filter (
            where status not in ('cancelled', 'returned')
        )                                            as net_revenue,

        -- Outlier-excluded measures. Extreme quantities (>100 units on a
        -- single line) are kept in the data but excluded here, because a
        -- few thousand-unit lines otherwise dominate every aggregate.
        sum(line_total) filter (
            where not is_outlier_quantity
        )                                            as gross_revenue_ex_outliers,
        sum(line_total) filter (
            where status not in ('cancelled', 'returned')
              and not is_outlier_quantity
        )                                            as net_revenue_ex_outliers,
        sum(quantity) filter (
            where not is_outlier_quantity
        )                                            as units_sold_ex_outliers,
        count(*) filter (where is_outlier_quantity)  as outlier_lines,

        count(*) filter (where status = 'returned')  as returned_lines

    from items
    group by product_id

),

review_stats as (

    select
        product_id,
        count(*)                                  as review_count,
        round(avg(rating), 2)                     as avg_rating,
        count(*) filter (where rating >= 4)       as positive_reviews,
        count(*) filter (where rating <= 2)       as negative_reviews,
        count(*) filter (where verified_purchase) as verified_reviews
    from {{ ref('stg_reviews') }}
    group by product_id

)

select
    p.product_id,
    p.product_name,
    p.category,
    p.price,
    p.cost,
    p.gross_margin,
    p.in_stock,
    p.is_margin_violation,
    p.is_uncategorized,

    coalesce(s.orders_containing, 0) as orders_containing,
    coalesce(s.units_sold, 0)        as units_sold,
    coalesce(s.net_units_sold, 0)    as net_units_sold,
    coalesce(s.gross_revenue, 0)     as gross_revenue,
    coalesce(s.net_revenue, 0)       as net_revenue,
    coalesce(s.gross_revenue_ex_outliers, 0) as gross_revenue_ex_outliers,
    coalesce(s.net_revenue_ex_outliers, 0)   as net_revenue_ex_outliers,
    coalesce(s.units_sold_ex_outliers, 0)    as units_sold_ex_outliers,
    coalesce(s.outlier_lines, 0)             as outlier_lines,

    -- Estimated gross profit on net units. Uses the current cost, which is
    -- an approximation: this model has no cost history, so a price change
    -- would retroactively alter historical profit. A real warehouse would
    -- snapshot cost at order time.
    round(coalesce(s.net_units_sold, 0) * p.gross_margin, 2) as est_gross_profit,

    coalesce(r.review_count, 0)      as review_count,
    r.avg_rating,
    coalesce(r.positive_reviews, 0)  as positive_reviews,
    coalesce(r.negative_reviews, 0)  as negative_reviews,
    coalesce(r.verified_reviews, 0)  as verified_reviews,

    coalesce(s.units_sold, 0) = 0    as has_never_sold

from products p
left join sales_stats  s on p.product_id = s.product_id
left join review_stats r on p.product_id = r.product_id
