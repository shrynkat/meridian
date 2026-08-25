"""Question templates for the Meridian baseline agent.

Each template pairs a regex against hand-written SQL. There is no generation
here — this is deliberately the dumbest thing that works, so that phase 6's
LLM has a floor to be measured against.

Every SQL string in this file is written against the semantic layer's
definitions: revenue defaults to line_total_ex_outliers on completed orders,
order counts come from fct_orders, units come from fct_order_items. The LLM
agent will be given the same semantic layer, so both agents are answering
from the same rules and their results are comparable.

The `measure` field on each template records which definition was used, so
the answer can state it. A revenue number without its definition is not an
answer.
"""

from dataclasses import dataclass, field


@dataclass
class Template:
    name: str
    patterns: list[str]
    sql: str
    measure: str | None = None
    notes: str = ""
    params: list[str] = field(default_factory=list)


TEMPLATES: list[Template] = [

    # -- Revenue ---------------------------------------------------------

    Template(
        name="total_revenue",
        patterns=[
            r"\b(total|overall)?\s*revenue\b(?!.*\b(by|per|category|segment|month|product|customer|state)\b)",
            r"how much (money|revenue) did we (make|earn)",
            r"what (are|were|is) (our|the) (total )?sales\b(?!.*\bby\b)",
        ],
        sql="""
            select
                round(sum(line_total_ex_outliers), 2) as revenue
            from gold.fct_order_items
            where is_completed
        """,
        measure="net_revenue_ex_outliers",
        notes="Completed orders only; outlier-quantity lines excluded.",
    ),

    Template(
        name="revenue_by_category",
        patterns=[
            r"revenue by (product )?category",
            r"which categor(y|ies) (make|makes|made|generate|generates|generated) the most",
            r"(sales|revenue) (per|by) categor",
            r"best (performing|selling) categor",
        ],
        sql="""
            select
                coalesce(p.category, '(uncategorised)') as category,
                round(sum(i.line_total_ex_outliers), 2) as revenue,
                count(distinct i.order_id)              as orders,
                sum(i.quantity) filter (
                    where not i.is_outlier_quantity
                )                                       as units
            from gold.fct_order_items i
            join gold.dim_products p on i.product_id = p.product_id
            where i.is_completed
            group by 1
            order by revenue desc
        """,
        measure="net_revenue_ex_outliers",
    ),

    Template(
        name="revenue_by_month",
        patterns=[
            r"revenue (by|per) month",
            r"monthly (revenue|sales)",
            r"revenue over time",
            r"(sales|revenue) trend",
        ],
        sql="""
            select
                order_month,
                round(sum(line_total_ex_outliers), 2) as revenue,
                count(distinct order_id)              as orders
            from gold.fct_order_items
            where is_completed
            group by 1
            order by 1
        """,
        measure="net_revenue_ex_outliers",
    ),

    Template(
        name="revenue_by_segment",
        patterns=[
            r"revenue by (customer )?segment",
            r"(consumer|business) (versus|vs\.?) (business|consumer)",
            r"which segment (spends|spent|generates|generated)",
        ],
        sql="""
            select
                customer_segment                       as segment,
                round(sum(line_total_ex_outliers), 2)  as revenue,
                count(distinct order_id)               as orders,
                count(distinct customer_id)            as customers
            from gold.obt_order_items
            where is_completed
            group by 1
            order by revenue desc
        """,
        measure="net_revenue_ex_outliers",
        notes="Uses the denormalised table: the question spans lines and customers.",
    ),

    Template(
        name="revenue_by_state",
        patterns=[
            r"revenue by state",
            r"which states? (buy|bought|spend|spent|generate|generated) the most",
            r"(sales|revenue) (by|per) (state|region|location)",
        ],
        sql="""
            select
                customer_state                        as state,
                round(sum(line_total_ex_outliers), 2) as revenue,
                count(distinct customer_id)           as customers
            from gold.obt_order_items
            where is_completed
            group by 1
            order by revenue desc
            limit 20
        """,
        measure="net_revenue_ex_outliers",
    ),

    # -- Orders ----------------------------------------------------------

    Template(
        name="order_count",
        patterns=[
            r"how many orders",
            r"(total )?(number|count) of orders",
            r"order (count|volume)",
        ],
        sql="""
            select
                count(*)                              as total_orders,
                count(*) filter (where is_completed)  as completed_orders,
                count(*) filter (where status = 'cancelled') as cancelled,
                count(*) filter (where status = 'returned')  as returned
            from gold.fct_orders
        """,
        notes="Counted from fct_orders, which is one row per order.",
    ),

    Template(
        name="average_order_value",
        patterns=[
            r"average order (value|size|total)",
            r"\baov\b",
            r"how much does (the average|a typical) (customer|order)",
            r"mean order value",
        ],
        sql="""
            with order_revenue as (
                select
                    order_id,
                    sum(line_total_ex_outliers) as order_revenue
                from gold.fct_order_items
                where is_completed
                group by order_id
            )
            select
                round(avg(order_revenue), 2)    as average_order_value,
                round(median(order_revenue), 2) as median_order_value,
                count(*)                        as orders_included
            from order_revenue
        """,
        measure="net_revenue_ex_outliers",
        notes=(
            "Aggregated to order grain first. Averaging line_total directly "
            "would give average line value, not average order value."
        ),
    ),

    Template(
        name="order_status_breakdown",
        patterns=[
            r"order status",
            r"how many (orders were|were) (cancelled|returned|delivered|shipped)",
            r"(cancellation|return) rate",
            r"status (breakdown|mix|distribution)",
        ],
        sql="""
            select
                status,
                count(*)                                       as orders,
                round(count(*) * 100.0 / sum(count(*)) over (), 2) as pct_of_orders
            from gold.fct_orders
            group by 1
            order by orders desc
        """,
    ),

    Template(
        name="average_basket_size",
        patterns=[
            r"(average|typical) basket",
            r"(how many|average number of) (items|products) per order",
            r"items per order",
            r"basket size",
        ],
        sql="""
            select
                round(avg(line_count), 2)    as avg_items_per_order,
                median(line_count)           as median_items_per_order,
                max(line_count)              as max_items_in_an_order
            from (
                select order_id, count(*) as line_count
                from gold.fct_order_items
                group by order_id
            )
        """,
    ),

    # -- Products --------------------------------------------------------

    Template(
        name="top_products",
        patterns=[
            r"(top|best|highest)[- ]?(selling|performing)? ?products?",
            r"which products? (sell|sold|sells) (the )?(best|most)",
            r"best sellers",
        ],
        sql="""
            select
                p.product_name,
                p.category,
                p.units_sold_ex_outliers               as units_sold,
                round(p.net_revenue_ex_outliers, 2)    as revenue,
                p.avg_rating
            from gold.dim_products p
            order by p.net_revenue_ex_outliers desc
            limit 10
        """,
        measure="net_revenue_ex_outliers",
    ),

    Template(
        name="worst_products",
        patterns=[
            r"(worst|lowest|poorest)[- ]?(selling|performing)? ?products?",
            r"which products? (sell|sold) (the )?(worst|least)",
            r"products? (that )?(never|have not|haven't) sold",
        ],
        sql="""
            select
                p.product_name,
                p.category,
                p.units_sold_ex_outliers            as units_sold,
                round(p.net_revenue_ex_outliers, 2) as revenue,
                p.has_never_sold
            from gold.dim_products p
            order by p.net_revenue_ex_outliers asc
            limit 10
        """,
        measure="net_revenue_ex_outliers",
    ),

    Template(
        name="product_ratings",
        patterns=[
            r"(highest|best|top) rated products?",
            r"which products? (have|has) the (best|highest) ratings?",
            r"average rating by (product|category)",
        ],
        sql="""
            select
                p.product_name,
                p.category,
                p.avg_rating,
                p.review_count
            from gold.dim_products p
            where p.review_count >= 5
            order by p.avg_rating desc, p.review_count desc
            limit 10
        """,
        notes="Requires at least 5 reviews, so a single 5-star review cannot top the list.",
    ),

    # -- Customers -------------------------------------------------------

    Template(
        name="top_customers",
        patterns=[
            r"(top|best|biggest|highest[- ]spending) customers?",
            r"which customers? (spend|spent|buy|bought) the most",
            r"most valuable customers?",
        ],
        sql="""
            select
                customer_id,
                first_name || ' ' || last_name          as customer,
                segment,
                state,
                total_orders,
                round(net_revenue_ex_outliers, 2)       as revenue
            from gold.dim_customers
            order by net_revenue_ex_outliers desc
            limit 10
        """,
        measure="net_revenue_ex_outliers",
    ),

    Template(
        name="customer_count",
        patterns=[
            r"how many customers",
            r"(total )?(number|count) of customers",
            r"customer count",
        ],
        sql="""
            select
                count(*)                                        as total_customers,
                count(*) filter (where total_orders > 0)         as customers_with_orders,
                count(*) filter (where has_never_ordered)        as never_ordered,
                count(*) filter (where is_repeat_customer)       as repeat_customers
            from gold.dim_customers
        """,
    ),

    Template(
        name="customers_by_segment",
        patterns=[
            r"customers? by segment",
            r"how many (consumer|business) customers",
            r"segment (breakdown|split|distribution)",
        ],
        sql="""
            select
                segment,
                count(*)                                  as customers,
                count(*) filter (where total_orders > 0)  as with_orders,
                round(avg(total_orders), 2)               as avg_orders,
                round(sum(net_revenue_ex_outliers), 2)    as revenue
            from gold.dim_customers
            group by 1
            order by revenue desc
        """,
        measure="net_revenue_ex_outliers",
    ),

    Template(
        name="repeat_customer_rate",
        patterns=[
            r"repeat (customer|purchase) rate",
            r"how many customers (order|ordered|buy|bought) (more than once|again)",
            r"customer retention",
        ],
        sql="""
            select
                count(*)                                     as total_customers,
                count(*) filter (where total_orders >= 2)    as ordered_twice_or_more,
                count(*) filter (where is_repeat_customer)   as five_or_more_orders,
                round(
                    count(*) filter (where total_orders >= 2) * 100.0
                    / nullif(count(*) filter (where total_orders > 0), 0),
                    2
                ) as repeat_rate_pct
            from gold.dim_customers
        """,
        notes="Repeat rate is computed over customers who ordered at least once.",
    ),

    # -- Clickstream -----------------------------------------------------

    Template(
        name="event_volume",
        patterns=[
            r"how many (events|page views|pageviews|sessions)",
            r"(event|traffic) volume",
            r"event (type )?(breakdown|mix|distribution)",
        ],
        sql="""
            select
                event_type,
                count(*)                       as events,
                count(distinct session_id)     as sessions,
                count(*) filter (where is_anonymous) as anonymous_events
            from silver.stg_events
            group by 1
            order by events desc
        """,
    ),

    Template(
        name="device_breakdown",
        patterns=[
            r"(device|mobile|desktop) (breakdown|mix|split|share)",
            r"how much traffic (is|comes from) mobile",
            r"traffic by device",
        ],
        sql="""
            select
                device_type,
                count(*)                                          as events,
                round(count(*) * 100.0 / sum(count(*)) over (), 2) as pct_of_events
            from silver.stg_events
            group by 1
            order by events desc
        """,
    ),

    Template(
        name="top_searches",
        patterns=[
            r"(top|most common|popular) search(es|s| terms| queries)?",
            r"what (do|are) (people|customers|users) search(ing)? for",
        ],
        sql="""
            select
                search_query,
                count(*)                    as searches,
                round(avg(results_count), 1) as avg_results
            from silver.stg_events
            where event_type = 'search'
              and search_query is not null
            group by 1
            order by searches desc
            limit 15
        """,
    ),

    # -- Reviews ---------------------------------------------------------

    Template(
        name="review_summary",
        patterns=[
            r"(how many|number of) reviews",
            r"(average|mean) rating\b(?!.*\bby (product|category)\b)",
            r"rating (breakdown|distribution)",
        ],
        sql="""
            select
                count(*)                                    as total_reviews,
                round(avg(rating), 2)                       as avg_rating,
                count(*) filter (where rating >= 4)         as positive,
                count(*) filter (where rating = 3)          as neutral,
                count(*) filter (where rating <= 2)         as negative,
                count(*) filter (where not verified_purchase) as unverified
            from silver.stg_reviews
        """,
    ),

    Template(
        name="rating_by_category",
        patterns=[
            r"(average |avg )?ratings? by categor",
            r"which categor(y|ies) (has|have) the (best|worst|highest|lowest) rating",
        ],
        sql="""
            select
                coalesce(p.category, '(uncategorised)') as category,
                round(avg(r.rating), 2)                 as avg_rating,
                count(*)                                as reviews
            from silver.stg_reviews r
            join gold.dim_products p on r.product_id = p.product_id
            group by 1
            order by avg_rating desc
        """,
    ),
]
