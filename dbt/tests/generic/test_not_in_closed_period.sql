{#- No postings in a closed period. A row fails when its date is in a closed period AND it was
    posted after the period was closed. Postings made before closing are fine. -#}
{% test not_in_closed_period(model, date_column, posted_column) %}

select
    m.*,
    p.period    as closed_period,
    p.closed_at
from {{ model }} as m
inner join {{ ref('accounting_periods') }} as p
    on m.{{ date_column }} between p.period_start and p.period_end
where p.status = 'closed'
  and m.{{ posted_column }} > p.closed_at

{% endtest %}
