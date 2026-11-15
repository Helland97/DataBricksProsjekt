{{ config(severity='warn', tags=['audit']) }}

-- GL lines posted to an account that was INACTIVE on the entry date. Uses the account version valid
-- on entry_date (SCD2 from dim_accounts), not today's chart of accounts.

select
    g.entry_id,
    g.voucher_no,
    g.entry_date,
    g.account_no,
    a.account_name,
    a.valid_from_date as inactive_since
from {{ ref('stg_erp__gl_entries') }} as g
inner join {{ ref('dim_accounts') }} as a
    on  a.account_no = g.account_no
    and g.entry_date >= a.valid_from_date
    and g.entry_date <  a.valid_to_date
where not a.is_active
