{#-
    Cleanup of the schemas CI's PR builds create (pr_<n>_silver, pr_<n>_gold, ...). Called by pr-cleanup.yml:
        dbt run-operation drop_pr_schemas --args "{prefix: pr_12_}"           when PR 12 closes
        dbt run-operation drop_stale_pr_schemas --args "{open_prs: [3, 7]}"   weekly: everything not open

    `drop schema ... cascade` can't be undone, so both are guarded twice: only schemas whose name matches
    pr_<number>_ are ever listed, and drop_pr_schemas refuses any prefix that isn't exactly pr_<number>_.
    An empty prefix would otherwise match silver and gold.
-#}

{% macro drop_pr_schemas(prefix) %}
    {% if not modules.re.fullmatch('pr_[0-9]+_', prefix | string) %}
        {{ exceptions.raise_compiler_error(
            "Refusing to drop schemas for prefix '" ~ prefix ~ "'. It must look like pr_<number>_."
        ) }}
    {% endif %}
    {% for schema in list_pr_schemas() if schema.startswith(prefix) %}
        {% do drop_schema_cascade(schema) %}
    {% else %}
        {% do log("no schemas start with " ~ prefix ~ ", nothing to drop", info=true) %}
    {% endfor %}
{% endmacro %}


{% macro drop_stale_pr_schemas(open_prs) %}
    {% set keep = open_prs | map('int') | list %}
    {% for schema in list_pr_schemas() %}
        {% set number = modules.re.match('pr_([0-9]+)_', schema).group(1) | int %}
        {% if number in keep %}
            {% do log("keeping " ~ schema ~ " (PR #" ~ number ~ " is open)", info=true) %}
        {% else %}
            {% do drop_schema_cascade(schema) %}
        {% endif %}
    {% else %}
        {% do log("no PR schemas, nothing to drop", info=true) %}
    {% endfor %}
{% endmacro %}


{% macro list_pr_schemas() %}
    {#- Every schema in the catalog whose name starts with pr_<number>_. Nothing else is ever returned. -#}
    {% set names = [] %}
    {% for row in run_query("show schemas in " ~ adapter.quote(target.database)) %}
        {% if modules.re.match('pr_[0-9]+_', row[0]) %}
            {% do names.append(row[0]) %}
        {% endif %}
    {% endfor %}
    {{ return(names) }}
{% endmacro %}


{% macro drop_schema_cascade(schema) %}
    {% set qualified = adapter.quote(target.database) ~ "." ~ adapter.quote(schema) %}
    {% do run_query("drop schema if exists " ~ qualified ~ " cascade") %}
    {% do log("dropped " ~ target.database ~ "." ~ schema, info=true) %}
{% endmacro %}
