{#- Drops both snapshot tables, so a replay starts history from scratch.
    Run with: dbt run-operation reset_snapshots -#}
{% macro reset_snapshots() %}
    {% for name in ['snp_erp__customers', 'snp_erp__accounts'] %}
        {#- generate_schema_name: in a PR build that's pr_<n>_silver, so the real snapshots are never touched -#}
        {% set schema = generate_schema_name('silver', none) %}
        {% set rel = adapter.get_relation(database=target.database, schema=schema, identifier=name) %}
        {% if rel %}
            {% do adapter.drop_relation(rel) %}
            {% do log('dropped ' ~ rel, info=true) %}
        {% else %}
            {% do log(name ~ ' does not exist, nothing to drop', info=true) %}
        {% endif %}
    {% endfor %}
{% endmacro %}
