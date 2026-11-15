-- One row per voucher (bilag). The voucher, not the line, is the unit that must balance.

with gl as (
    select * from {{ ref('stg_erp__gl_entries') }}
)

select
    voucher_no,
    min(entry_date)             as entry_date,
    min(posted_at)              as posted_at,
    max(loaded_at)              as last_loaded_at,
    any_value(description)      as description,
    any_value(invoice_id)       as invoice_id,
    count(*)                    as line_count,
    sum(debit)                  as total_debit,
    sum(credit)                 as total_credit,
    sum(debit) - sum(credit)    as imbalance,
    sum(debit) = sum(credit)    as is_balanced
from gl
group by voucher_no
