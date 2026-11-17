{#-
    +schema is used as-is ("silver", not "silver_silver"), with an optional prefix in front.
    DBT_SCHEMA_PREFIX is only set by CI (dbt-pr.yml): PR 12 builds into pr_12_silver, pr_12_gold, pr_12_audit,
    so a PR never touches the real schemas. Locally and in the Databricks job it's empty: nothing changes.
    Sources aren't affected (_erp__sources.yml names the schema directly), so PRs read the real bronze.
-#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set base = target.schema if custom_schema_name is none else custom_schema_name | trim -%}
    {{ env_var('DBT_SCHEMA_PREFIX', '') ~ base }}
{%- endmacro %}
