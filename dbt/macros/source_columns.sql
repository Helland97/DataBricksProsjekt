{#-
    Bronze looks different before and after the day 15 schema change: before it, invoices has no
    `currency`, `kid` or `amount_incl_vat__v_text`, and dlt hasn't created the child table
    `invoices__lines` yet. These two macros let the staging models work on both.

    They only ask the warehouse at execution time. At parse time (`dbt parse` in CI, no connection)
    they return true, so every source() call is still parsed and shows up in the lineage.
    Unit tests override source_has_column to true: their fixtures define the columns themselves.
-#}

{% macro source_has_column(source_name, table_name, column_name) -%}
    {%- if not execute -%}
        {{ return(true) }}
    {%- endif -%}
    {%- set columns = adapter.get_columns_in_relation(source(source_name, table_name)) -%}
    {{ return(column_name | lower in (columns | map(attribute='name') | map('lower') | list)) }}
{%- endmacro %}


{% macro source_exists(source_name, table_name) -%}
    {%- if not execute -%}
        {{ return(true) }}
    {%- endif -%}
    {%- set src = source(source_name, table_name) -%}
    {%- set relation = adapter.get_relation(database=src.database, schema=src.schema, identifier=src.identifier) -%}
    {{ return(relation is not none) }}
{%- endmacro %}
