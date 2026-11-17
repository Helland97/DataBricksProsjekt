{#- dlt creates the child table invoices__lines when the first invoice with lines arrives (day 15).
    Until then this model is an empty table with the right columns, so everything downstream builds. -#}
{% if source_exists('erp', 'invoices__lines') %}

with invoices as (
    select _dlt_id, invoice_id
    from {{ source('erp', 'invoices') }}
    qualify row_number() over (
        partition by invoice_id
        order by updated_at desc, _dlt_load_id desc
    ) = 1
),

lines as (
    select * from {{ source('erp', 'invoices__lines') }}
)

select
    concat(invoices.invoice_id, '-', lines.line_no)  as invoice_line_id,
    invoices.invoice_id,
    cast(lines.line_no as int)                       as line_no,
    trim(lines.description)                          as description,
    cast(lines.quantity as int)                      as quantity,
    cast(lines.unit_price as decimal(18, 2))         as unit_price,
    cast(lines.vat_rate as int)                      as vat_rate,
    cast(lines.amount_ex_vat as decimal(18, 2))      as amount_ex_vat
from lines
inner join invoices
    on lines._dlt_parent_id = invoices._dlt_id

{% else %}

select
    cast(null as string)        as invoice_line_id,
    cast(null as bigint)        as invoice_id,
    cast(null as int)           as line_no,
    cast(null as string)        as description,
    cast(null as int)           as quantity,
    cast(null as decimal(18, 2)) as unit_price,
    cast(null as int)           as vat_rate,
    cast(null as decimal(18, 2)) as amount_ex_vat
where false

{% endif %}
