{% macro parse_nok_amount(column) -%}
    try_cast(
        replace(regexp_replace({{ column }}, '[^0-9,-]', ''), ',', '.')
        as decimal(18, 2)
    )
{%- endmacro %}
