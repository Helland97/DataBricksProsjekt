{#- No gaps in a number series (invoice numbers, voucher numbers). Returns each gap as a range
    gap_from-gap_to, because the auditor wants to know WHICH numbers are missing. -#}
{% test no_sequence_gaps(model, column_name) %}

with numbers as (
    select distinct {{ column_name }} as n
    from {{ model }}
    where {{ column_name }} is not null
),

with_next as (
    select n, lead(n) over (order by n) as next_n
    from numbers
)

select
    n + 1          as gap_from,
    next_n - 1     as gap_to,
    next_n - n - 1 as missing_count
from with_next
where next_n - n > 1

{% endtest %}
