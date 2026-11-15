{{ config(severity='warn', tags=['audit']) }}

-- BUSINESS duplicates: two valid invoices to the same customer with the same amount and the same
-- lines within 14 days, but different numbers. `unique` on invoice_id only catches TECHNICAL
-- duplicates (the same row twice). Credited invoices are left out, because crediting and
-- re-invoicing is legitimate.
-- v1 invoices (before day 15) have no lines: <=> treats null = null, so they're compared on amount.

with line_signatures as (
    select
        invoice_id,
        concat_ws('|', array_sort(collect_list(
            concat_ws(';', description, cast(quantity as string), cast(unit_price as string))
        ))) as line_signature
    from {{ ref('stg_erp__invoice_lines') }}
    group by invoice_id
),

invoices as (
    select i.invoice_id, i.customer_id, i.invoice_date, i.amount_incl_vat, l.line_signature
    from {{ ref('stg_erp__invoices') }} as i
    left join line_signatures as l
        on l.invoice_id = i.invoice_id
    where i.status <> 'credited'
)

select
    a.invoice_id                            as first_invoice_id,
    b.invoice_id                            as duplicate_invoice_id,
    a.customer_id,
    a.invoice_date                          as first_invoice_date,
    b.invoice_date                          as duplicate_invoice_date,
    datediff(b.invoice_date, a.invoice_date) as days_between,
    a.amount_incl_vat
from invoices as a
inner join invoices as b
    on  b.customer_id = a.customer_id
    and b.amount_incl_vat = a.amount_incl_vat
    and b.line_signature <=> a.line_signature
    and b.invoice_id > a.invoice_id
    and datediff(b.invoice_date, a.invoice_date) between 0 and 14
