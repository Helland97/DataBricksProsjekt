-- The trial balance must net to zero in every period: total debit = total credit, and the sum of all
-- closing balances = 0. If not, gold is wrong (an unbalanced voucher got through, or the spine broke).
-- Severity: error.
select
    period,
    sum(debit)           as total_debit,
    sum(credit)          as total_credit,
    sum(closing_balance) as total_closing
from {{ ref('rpt_trial_balance') }}
group by period
having sum(debit) <> sum(credit)
    or sum(closing_balance) <> 0
