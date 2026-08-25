{#
    Override dbt's default schema naming.

    By default dbt produces <target_schema>_<custom_schema>, giving
    main_gold, main_silver, main_quarantine. That prefix is a dbt
    implementation detail, and it appears in every query the text-to-SQL
    agent writes. Using the custom schema name directly gives gold, silver,
    and quarantine — which is what the semantic layer describes and what a
    real warehouse would look like.

    Safe here because this project has a single target. On a project with
    dev and prod targets sharing one database, the prefix is what keeps
    them apart, and removing it would be a mistake.
#}

{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
