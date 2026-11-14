{% macro dlt_loaded_at(column) -%}
    timestamp_seconds(cast({{ column }} as decimal(20, 6)))
{%- endmacro %}
