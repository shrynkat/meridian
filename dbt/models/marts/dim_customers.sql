-- Gold: customer dimension. One row per customer.
--
-- GRAIN: one row per customer_id.
--
-- Revenue is exposed twice, deliberately:
--   gross_revenue  every order, whatever its status
--   net_revenue    excluding cancelled and returned orders
-- Both are legitimate. Finance usually means net; demand planning usually
-- means gross. A single column named "revenue" would silently pick one on
-- behalf of everyone querying this table, so it does not exist here.

with customers as (

    select * from {{ ref('stg_customers') }}

),

orders as (

    select * from {{ ref('stg_orders') }}

),

-- Per-order outlier exposure, so customer revenue can be reported with and
-- without the lines whose quantities are implausible.
-- Revenue is summed from line items, not from the header's order_total.
-- The header total is unreliable: 6,365 orders have a stored total that
-- disagrees with the sum of their lines, because defect injection modified
-- line quantities without recomputing the header. Deriving from lines makes
-- the outlier exclusion arithmetically sound.
order_lines as (

    select
        order_id,
        sum(line_total)                                     as line_revenue,
        sum(case when is_outlier_quantity then 0 else line_total end)
                                                            as line_revenue_ex_outliers,
        count(*) filter (where is_outlier_quantity)         as outlier_lines
    from {{ ref('stg_order_items') }}
    group by order_id

),

order_stats as (

    select
        o.customer_id,

        count(*)                                         as total_orders,
        count(*) filter (where o.status = 'delivered')   as delivered_orders,
        count(*) filter (where o.status = 'cancelled')   as cancelled_orders,
        count(*) filter (where o.status = 'returned')    as returned_orders,

        sum(o.order_total)                               as gross_revenue,
        sum(o.order_total) filter (
            where o.status not in ('cancelled', 'returned')
        )                                                as net_revenue,

        sum(coalesce(x.line_revenue_ex_outliers, 0))     as gross_revenue_ex_outliers,
        sum(coalesce(x.line_revenue_ex_outliers, 0)) filter (
            where o.status not in ('cancelled', 'returned')
        )                                                as net_revenue_ex_outliers,
        sum(coalesce(x.line_revenue, 0))                 as line_revenue,
        sum(coalesce(x.outlier_lines, 0))                as outlier_lines,

        sum(o.shipping_cost)                             as total_shipping_paid,
        sum(o.item_count)                                as total_items_ordered,

        min(o.order_date)                                as first_order_date,
        max(o.order_date)                                as last_order_date

    from orders o
    left join order_lines x on o.order_id = x.order_id
    group by o.customer_id

)

select
    c.customer_id,
    c.first_name,
    c.last_name,
    c.email,
    c.city,
    c.state,
    c.country,
    c.segment,
    c.marketing_opt_in,
    c.signup_date,

    coalesce(s.total_orders, 0)      as total_orders,
    coalesce(s.delivered_orders, 0)  as delivered_orders,
    coalesce(s.cancelled_orders, 0)  as cancelled_orders,
    coalesce(s.returned_orders, 0)   as returned_orders,

    coalesce(s.gross_revenue, 0)     as gross_revenue,
    coalesce(s.net_revenue, 0)       as net_revenue,
    coalesce(s.gross_revenue_ex_outliers, 0) as gross_revenue_ex_outliers,
    coalesce(s.net_revenue_ex_outliers, 0)   as net_revenue_ex_outliers,
    coalesce(s.line_revenue, 0)              as line_revenue,
    coalesce(s.outlier_lines, 0)             as outlier_lines,
    coalesce(s.total_shipping_paid, 0) as total_shipping_paid,
    coalesce(s.total_items_ordered, 0) as total_items_ordered,

    -- Average order value, net. Null rather than zero for customers who
    -- never ordered — an average over no orders is undefined, not zero,
    -- and zero would drag down any aggregate computed over this column.
    case
        when coalesce(s.total_orders, 0) - coalesce(s.cancelled_orders, 0)
             - coalesce(s.returned_orders, 0) > 0
        then round(
            s.net_revenue / (s.total_orders - s.cancelled_orders - s.returned_orders),
            2
        )
    end as avg_order_value,

    case
        when coalesce(s.total_orders, 0) > 0
        then round(s.returned_orders::double / s.total_orders, 4)
    end as return_rate,

    s.first_order_date,
    s.last_order_date,

    date_diff('day', c.signup_date, current_date)        as tenure_days,
    date_diff('day', s.last_order_date, current_date)    as days_since_last_order,

    coalesce(s.total_orders, 0) = 0                      as has_never_ordered,
    coalesce(s.total_orders, 0) >= 5                     as is_repeat_customer

from customers c
left join order_stats s on c.customer_id = s.customer_id
