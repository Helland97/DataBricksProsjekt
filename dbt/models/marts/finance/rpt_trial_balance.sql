-- Trial balance (saldobalanse). Grain: period (month) x account.
-- Sign: debit positive. Assets/expenses are usually positive, liabilities/equity/revenue negative.
-- Balance sheet accounts (class 1-2) carry their balance forward. P&L accounts (class 3-8) start
-- from 0 each financial year.

with periods as (
    select period, period_start, year(period_start) as fiscal_year
    from {{ ref('accounting_periods') }}
    where period_start <= (select max(entry_date) from {{ ref('fct_gl_entries') }})
),

accounts as (
    select account_no, account_name, account_type, account_class
    from {{ ref('dim_accounts') }}
    where is_current
),

-- Every period x every account, so an account with no activity still shows its balance.
spine as (
    select p.period, p.fiscal_year, a.*
    from periods as p
    cross join accounts as a
),

movements as (
    select period, account_no, sum(debit) as debit, sum(credit) as credit
    from {{ ref('fct_gl_entries') }}
    group by period, account_no
),

with_movements as (
    select
        s.*,
        coalesce(m.debit, 0)                         as debit,
        coalesce(m.credit, 0)                        as credit,
        coalesce(m.debit, 0) - coalesce(m.credit, 0) as net_change
    from spine as s
    left join movements as m
        on m.period = s.period
       and m.account_no = s.account_no
),

balances as (
    select
        *,
        sum(net_change) over (
            partition by account_no,
                         case when account_class <= 2 then 0 else fiscal_year end
            order by period
            rows between unbounded preceding and current row
        ) as closing_balance
    from with_movements
)

select
    cast(period as string)                            as period,
    cast(account_no as int)                           as account_no,
    cast(account_name as string)                      as account_name,
    cast(account_type as string)                      as account_type,
    cast(account_class as int)                        as account_class,
    cast(closing_balance - net_change as decimal(18, 2)) as opening_balance,
    cast(debit as decimal(18, 2))                     as debit,
    cast(credit as decimal(18, 2))                    as credit,
    cast(net_change as decimal(18, 2))                as net_change,
    cast(closing_balance as decimal(18, 2))           as closing_balance
from balances
