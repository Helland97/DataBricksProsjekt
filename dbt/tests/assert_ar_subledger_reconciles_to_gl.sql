-- Sub-ledger vs general ledger (kundereskontro mot hovedbok). Severity: error.
--  1) Total: sum of rpt_ar_aging = GL balance on the AR account on as_of_date.
--  2) Per invoice: open amount from the GL = what the invoice status says
--     (sent/overdue = the full amount, paid/credited = 0). Only on the latest date, because
--     status is the CURRENT status and can't be compared with the GL at an earlier date.
--     Invoices with a voucher in quarantine (unbalanced, a GL line hasn't arrived yet) are left out:
--     the GL side is incomplete on purpose (only balanced vouchers count), so they are reconciling
--     items, not errors. assert_no_stale_unbalanced_vouchers fails if one waits longer than allowed.
--     Found by the first scheduled run (STEG5): it saw bronze mid-way, before the late lines arrived.

{%- set as_of_date = var('as_of_date') %}

with as_of as (
    select max(as_of_date) as as_of_date from {{ ref('int_ar_open_items') }}
),

subledger as (
    select coalesce(sum(open_amount), 0) as amount from {{ ref('rpt_ar_aging') }}
),

ledger as (
    select coalesce(sum(g.amount), 0) as amount
    from {{ ref('fct_gl_entries') }} as g
    cross join as_of
    where g.account_no = {{ var('ar_account_no') }}
      and g.entry_date <= as_of.as_of_date
),

total_check as (
    select
        'total'                  as check_name,
        cast(null as bigint)     as invoice_id,
        s.amount                 as expected_amount,
        l.amount                 as gl_amount
    from subledger as s
    cross join ledger as l
    where s.amount <> l.amount
)

{% if not as_of_date %}
, in_quarantine as (
    -- Invoices with at least one unbalanced voucher: the invoice posting, a payment or a credit note.
    select distinct v.invoice_id
    from {{ ref('int_gl_vouchers') }} as v
    where not v.is_balanced
      and v.invoice_id is not null
),

per_invoice_check as (
    select
        'invoice'                as check_name,
        i.invoice_id,
        case when i.status in ('sent', 'overdue') then i.amount_incl_vat else 0 end as expected_amount,
        coalesce(o.open_amount, 0) as gl_amount
    from {{ ref('fct_invoices') }} as i
    left join {{ ref('int_ar_open_items') }} as o
        on o.invoice_id = i.invoice_id
    left join in_quarantine as q
        on q.invoice_id = i.invoice_id
    where q.invoice_id is null
      and case when i.status in ('sent', 'overdue') then i.amount_incl_vat else 0 end
          <> coalesce(o.open_amount, 0)
)

select * from total_check
union all
select * from per_invoice_check
{% else %}
select * from total_check
{% endif %}
