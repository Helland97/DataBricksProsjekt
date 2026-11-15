-- AR sub-ledger (kundereskontro) built from the GL: what is still open on each invoice,
-- as of var('as_of_date'). Built from the GL (the book of record), not from the invoice status,
-- so the two can be reconciled against each other (STEG3-PLAN.md, section 9).
--
-- Only BALANCED vouchers, the same population as fct_gl_entries. Then the AR sub-ledger and the
-- trial balance reconcile exactly, even while a voucher with a late line waits in quarantine.

{%- set as_of_date = var('as_of_date') %}

with balanced_vouchers as (
    select voucher_no
    from {{ ref('int_gl_vouchers') }}
    where is_balanced
),

gl as (
    select gl.*
    from {{ ref('stg_erp__gl_entries') }} as gl
    inner join balanced_vouchers as v
        on v.voucher_no = gl.voucher_no
),

params as (
    select
        {% if as_of_date -%}
        cast('{{ as_of_date }}' as date)
        {%- else -%}
        (select max(entry_date) from gl)
        {%- endif %} as as_of_date
),

-- A voucher that credits AR and debits the bank is a customer payment.
-- Anything else that credits AR is a credit note (kreditnota).
payment_vouchers as (
    select distinct voucher_no
    from gl
    where account_no = {{ var('bank_account_no') }}
      and debit > 0
),

ar_lines as (
    select
        gl.*,
        params.as_of_date,
        p.voucher_no is not null as is_payment
    from gl
    cross join params
    left join payment_vouchers as p
        on p.voucher_no = gl.voucher_no
    where gl.account_no = {{ var('ar_account_no') }}
      and gl.entry_date <= params.as_of_date
)

select
    invoice_id,
    any_value(customer_id)                                   as customer_id,
    any_value(as_of_date)                                    as as_of_date,
    sum(debit)                                               as invoiced_amount,
    sum(credit)                                              as settled_amount,
    sum(case when is_payment then credit else 0 end)         as paid_amount,
    sum(case when not is_payment then credit else 0 end)     as credited_amount,
    sum(debit) - sum(credit)                                 as open_amount,
    sum(debit) <> sum(credit)                                as is_open,
    min(entry_date)                                          as first_entry_date,
    max(entry_date)                                          as last_entry_date,
    max(case when is_payment then entry_date end)            as last_payment_date
from ar_lines
group by invoice_id
