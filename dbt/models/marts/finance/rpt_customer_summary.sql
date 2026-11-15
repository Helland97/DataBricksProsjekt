-- Summary per customer (oppsummering per kunde). Grain: customer (current version).

with customers as (
    select * from {{ ref('dim_customers') }}
    where is_current
),

per_invoice as (
    select
        i.customer_id,
        i.invoice_id,
        i.invoice_date,
        i.amount_incl_vat,
        coalesce(ar.paid_amount, 0)                                  as paid_amount,
        coalesce(ar.credited_amount, 0)                              as credited_amount,
        coalesce(ar.open_amount, 0)                                  as open_amount,
        case when ag.days_past_due > 0 then ag.open_amount else 0 end as overdue_amount,
        case when ar.paid_amount > 0
             then datediff(ar.last_payment_date, i.invoice_date) end  as days_to_payment
    from {{ ref('fct_invoices') }} as i
    left join {{ ref('int_ar_open_items') }} as ar
        on ar.invoice_id = i.invoice_id
    left join {{ ref('rpt_ar_aging') }} as ag
        on ag.invoice_id = i.invoice_id
),

per_customer as (
    select
        customer_id,
        count(*)               as invoice_count,
        sum(amount_incl_vat)   as invoiced_amount,
        sum(credited_amount)   as credited_amount,
        sum(paid_amount)       as paid_amount,
        sum(open_amount)       as open_amount,
        sum(overdue_amount)    as overdue_amount,
        avg(days_to_payment)   as avg_days_to_payment,
        max(invoice_date)      as last_invoice_date
    from per_invoice
    group by customer_id
)

select
    cast(c.customer_id as bigint)                                    as customer_id,
    cast(c.customer_name as string)                                  as customer_name,
    cast(c.city as string)                                           as city,
    cast(c.payment_terms_days as int)                                as payment_terms_days,
    cast(coalesce(p.invoice_count, 0) as int)                        as invoice_count,
    cast(coalesce(p.invoiced_amount, 0) as decimal(18, 2))           as invoiced_amount,
    cast(coalesce(p.credited_amount, 0) as decimal(18, 2))           as credited_amount,
    cast(coalesce(p.paid_amount, 0) as decimal(18, 2))               as paid_amount,
    cast(coalesce(p.open_amount, 0) as decimal(18, 2))               as open_amount,
    cast(coalesce(p.overdue_amount, 0) as decimal(18, 2))            as overdue_amount,
    cast(p.overdue_amount / nullif(p.open_amount, 0) as decimal(5, 4)) as overdue_share,
    cast(p.avg_days_to_payment as decimal(6, 1))                     as avg_days_to_payment,
    cast(p.last_invoice_date as date)                                as last_invoice_date
from customers as c
left join per_customer as p
    on p.customer_id = c.customer_id
