-- One row per invoice, joined to the customer VERSION valid on invoice_date (point-in-time join):
-- the address and payment terms at the time of invoicing, which is what an auditor wants to see.

with invoices as (
    select * from {{ ref('stg_erp__invoices') }}
),

customers as (
    select * from {{ ref('dim_customers') }}
)

select
    i.invoice_id,
    i.customer_id,
    c.customer_sk,
    i.invoice_date,
    -- due_date is null for some invoices (null mess). Derive it from the payment terms the customer
    -- had ON THE INVOICE DATE, not today's terms.
    coalesce(i.due_date, date_add(i.invoice_date, c.payment_terms_days)) as due_date,
    i.due_date is null                                                    as is_due_date_derived,
    c.payment_terms_days,
    i.amount_ex_vat,
    i.vat_amount,
    i.amount_incl_vat,
    i.is_vat_derived,
    i.is_amount_parsed_from_text,
    i.currency,
    i.kid,
    i.status,
    i.updated_at                                                          as status_updated_at
from invoices as i
-- LEFT join on purpose: an invoice with no matching customer version keeps customer_sk = null,
-- and the not_null test reports it (flag, don't drop).
left join customers as c
    on  c.customer_id = i.customer_id
    and i.invoice_date >= c.valid_from_date
    and i.invoice_date <  c.valid_to_date
