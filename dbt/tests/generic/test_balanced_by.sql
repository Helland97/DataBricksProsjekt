{#- Debit = credit per group (e.g. per voucher). Returns every group that doesn't balance.
    Exact, because the amounts are decimal(18,2), not floats. -#}
{% test balanced_by(model, group_by, debit='debit', credit='credit') %}

select
    {{ group_by }},
    sum({{ debit }})                     as total_debit,
    sum({{ credit }})                    as total_credit,
    sum({{ debit }}) - sum({{ credit }}) as imbalance
from {{ model }}
group by {{ group_by }}
having sum({{ debit }}) <> sum({{ credit }})

{% endtest %}
