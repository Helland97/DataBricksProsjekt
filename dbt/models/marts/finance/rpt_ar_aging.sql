-- Aged accounts receivable (aldersfordelt kundereskontro). Grain: open invoice on as_of_date.
-- The open amount comes from the GL (int_ar_open_items), NOT from the invoice status.
-- as_of_date = var('as_of_date'), default the latest entry_date. Never current_date(): the data is
-- simulated, and "today" would age everything by weeks.

with open_items as (
    select * from {{ ref('int_ar_open_items') }}
    where is_open
),

invoices as (
    select invoice_id, invoice_date, due_date
    from {{ ref('fct_invoices') }}
),

customers as (
    select customer_id, customer_name
    from {{ ref('dim_customers') }}
    where is_current
),

aged as (
    select
        o.as_of_date,
        o.invoice_id,
        o.customer_id,
        c.customer_name,
        i.invoice_date,
        i.due_date,
        o.open_amount,
        datediff(o.as_of_date, i.due_date) as days_past_due
    from open_items as o
    left join invoices as i
        on i.invoice_id = o.invoice_id
    left join customers as c
        on c.customer_id = o.customer_id
)

select
    cast(as_of_date as date)              as as_of_date,
    cast(invoice_id as bigint)            as invoice_id,
    cast(customer_id as bigint)           as customer_id,
    cast(customer_name as string)         as customer_name,
    cast(invoice_date as date)            as invoice_date,
    cast(due_date as date)                as due_date,
    cast(open_amount as decimal(18, 2))   as open_amount,
    cast(days_past_due as int)            as days_past_due,
    -- No catch-all ELSE: a missing due_date gives a null bucket, and the contract fails loudly
    -- instead of silently putting the invoice in "90+".
    case
        when days_past_due <= 0  then 'not_due'
        when days_past_due <= 30 then '1-30'
        when days_past_due <= 60 then '31-60'
        when days_past_due <= 90 then '61-90'
        when days_past_due > 90  then '90+'
    end                                   as aging_bucket
from aged
