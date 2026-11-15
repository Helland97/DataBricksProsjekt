{#-
    Overrides dbt's built-in snapshot_get_time(), which the check strategy (and hard_deletes)
    uses as the version timestamp. Normally that's current_timestamp(). During a replay of
    simulated days we pass --vars "{snapshot_time: '2026-10-10'}", so versions get the
    simulated date instead of the wall-clock date. Leave it unset in production.
-#}
{% macro snapshot_get_time() -%}
    {%- if var('snapshot_time', none) -%}
        cast('{{ var("snapshot_time") }}' as timestamp)
    {%- else -%}
        {{ current_timestamp() }}
    {%- endif -%}
{%- endmacro %}
