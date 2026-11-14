{% macro is_valid_org_no(column) -%}
    {%- set weights = [3, 2, 7, 6, 5, 4, 3, 2] -%}
    case
        when {{ column }} rlike '^[0-9]{9}$' then
            mod(11 - mod(
                {%- for w in weights %}
                cast(substr({{ column }}, {{ loop.index }}, 1) as int) * {{ w }}{{ " +" if not loop.last }}
                {%- endfor %}
            , 11), 11) = cast(substr({{ column }}, 9, 1) as int)
        else false
    end
{%- endmacro %}
