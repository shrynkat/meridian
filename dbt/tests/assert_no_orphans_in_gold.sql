-- Every foreign key in the gold fact tables must resolve to a dimension.
--
-- Gold is what the text-to-SQL agent queries. An unresolvable key there
-- does not raise an error — it silently drops rows from an inner join, or
-- produces nulls in an outer one, and the agent reports a wrong number
-- with full confidence. Silver tolerates some orphans by design; gold
-- must not.

with order_customer_orphans as (
    select
        'fct_orders.customer_id' as broken_reference,
        f.customer_id            as missing_key,
        count(*)                 as affected_rows
    from {{ ref('fct_orders') }} f
    left join {{ ref('dim_customers') }} d on f.customer_id = d.customer_id
    where d.customer_id is null
    group by f.customer_id
),

item_product_orphans as (
    select
        'fct_order_items.product_id' as broken_reference,
        f.product_id                 as missing_key,
        count(*)                     as affected_rows
    from {{ ref('fct_order_items') }} f
    left join {{ ref('dim_products') }} d on f.product_id = d.product_id
    where d.product_id is null
    group by f.product_id
),

item_order_orphans as (
    select
        'fct_order_items.order_id' as broken_reference,
        f.order_id                 as missing_key,
        count(*)                   as affected_rows
    from {{ ref('fct_order_items') }} f
    left join {{ ref('fct_orders') }} o on f.order_id = o.order_id
    where o.order_id is null
    group by f.order_id
)

select * from order_customer_orphans
union all
select * from item_product_orphans
union all
select * from item_order_orphans
