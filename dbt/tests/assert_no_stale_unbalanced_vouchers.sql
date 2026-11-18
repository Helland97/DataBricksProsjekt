{{ config(severity='error', tags=['audit']) }}

-- No voucher (bilag) may wait in quarantine longer than the source's agreed lateness. Severity: error.
--
-- A voucher is unbalanced while one of its GL lines hasn't arrived yet. That's normal for a few days:
-- the source sends some lines late (up to var('gl_max_late_days') days after posting), and until then the
-- voucher is kept out of fct_gl_entries and the AR sub-ledger, and assert_ar_subledger_reconciles_to_gl
-- treats its invoice as a reconciling item. This test is what keeps that from hiding real problems:
-- a line that never arrives, or a voucher that simply doesn't balance, fails here once the wait is over.
--
-- Measured in SIMULATED time: "now" is the latest posted_at in the GL, not the wall clock. loaded_at is
-- real arrival time, and replay.ps1 loads 30 simulated days in minutes, so a wall-clock rule would be wrong.

with now as (
    select max(posted_at) as posted_at from {{ ref('stg_erp__gl_entries') }}
)

select
    v.voucher_no,
    v.invoice_id,
    v.description,
    v.line_count,
    v.total_debit,
    v.total_credit,
    v.imbalance,
    cast(v.posted_at as date)                                    as posted_date,
    cast(now.posted_at as date)                                  as simulated_today,
    datediff(cast(now.posted_at as date), cast(v.posted_at as date)) as days_waiting
from {{ ref('int_gl_vouchers') }} as v
cross join now
where not v.is_balanced
  and datediff(cast(now.posted_at as date), cast(v.posted_at as date)) > {{ var('gl_max_late_days') }}
