-- Line items absent from gold because their parent order was quarantined.
--
-- These rows pass every check of their own: valid product, valid quantity,
-- a real order_id. They disappear because fct_order_items inner-joins to
-- fct_orders, and their parent order was rejected for an orphan customer_id.
--
-- Without this model they are untraceable: present in silver, absent from
-- gold, recorded nowhere. A row that vanishes should be attributable to a
-- rule, which is the whole argument for having a quarantine layer.

select
    i.order_item_id,
    i.order_id,
    i.product_id,
    i.quantity,
    i.line_total,
    'parent_order_quarantined' as rejection_reason,
    current_timestamp          as _quarantined_at,
    i._source_file
from {{ ref('stg_order_items') }} i
left join {{ ref('stg_orders') }} o on i.order_id = o.order_id
where o.order_id is null
