-- The aging report pivoted: one row per customer, one column per bucket. This is the classic
-- layout a collector or auditor reads.

select
    customer_id,
    any_value(customer_name)                                                              as customer_name,
    any_value(as_of_date)                                                                 as as_of_date,
    count(*)                                                                              as open_invoice_count,
    cast(sum(open_amount) as decimal(18, 2))                                              as total_open,
    cast(sum(case when aging_bucket = 'not_due' then open_amount else 0 end) as decimal(18, 2)) as not_due,
    cast(sum(case when aging_bucket = '1-30'    then open_amount else 0 end) as decimal(18, 2)) as days_1_30,
    cast(sum(case when aging_bucket = '31-60'   then open_amount else 0 end) as decimal(18, 2)) as days_31_60,
    cast(sum(case when aging_bucket = '61-90'   then open_amount else 0 end) as decimal(18, 2)) as days_61_90,
    cast(sum(case when aging_bucket = '90+'     then open_amount else 0 end) as decimal(18, 2)) as days_90_plus
from {{ ref('rpt_ar_aging') }}
group by customer_id
