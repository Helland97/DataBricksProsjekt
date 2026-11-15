-- AR sub-ledger (kundereskontro) built from the GL: what is still open on each invoice,
-- as of var('as_of_date'). Built from the GL (the book of record), not from the invoice status,
-- so the two can be reconciled against each other (STEG3-PLAN.md, section 9).

{%- set as_of_date = var('as_of_date') %}

with gl as (
    select * from {{ ref('stg_erp__gl_entries') }}
),

params as (
    select
        {% if as_of_date -%}
        cast('{{ as_of_date }}' as date)
        {%- else -%}
        (select max(entry_date) from gl)
        {%- endif %} as as_of_date
),

ar_lines as (
    select
        gl.*,
        params.as_of_date
    from gl
    cross join params
    where gl.account_no = {{ var('ar_account_no') }}
      and gl.entry_date <= params.as_of_date
)

select
    invoice_id,
    any_value(customer_id)       as customer_id,
    any_value(as_of_date)        as as_of_date,
    sum(debit)                   as invoiced_amount,
    sum(credit)                  as settled_amount,
    sum(debit) - sum(credit)     as open_amount,
    sum(debit) <> sum(credit)    as is_open,
    min(entry_date)              as first_entry_date,
    max(entry_date)              as last_entry_date
from ar_lines
group by invoice_id
