{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='entry_id',
    on_schema_change='append_new_columns'
) }}

-- General ledger lines (hovedbok) in gold. ONLY lines from balanced vouchers.
-- A voucher with a late line waits in silver (quarantine) until the missing line arrives. The next
-- run then picks up the whole voucher, because the filter selects vouchers where ANY line arrived
-- since the last run. Gold never shows a trial balance with half a voucher.

with vouchers as (
    select voucher_no, last_loaded_at
    from {{ ref('int_gl_vouchers') }}
    where is_balanced
    {% if is_incremental() %}
      and last_loaded_at > (select max(voucher_loaded_at) from {{ this }})
    {% endif %}
),

gl as (
    select * from {{ ref('stg_erp__gl_entries') }}
)

select
    gl.entry_id,
    gl.voucher_no,
    gl.line_no,
    gl.entry_date,
    date_format(gl.entry_date, 'yyyy-MM')        as period,
    gl.account_no,
    gl.description,
    gl.debit,
    gl.credit,
    cast(gl.debit - gl.credit as decimal(18, 2)) as amount,
    gl.invoice_id,
    gl.customer_id,
    gl.posted_at,
    gl.loaded_at,
    vouchers.last_loaded_at                      as voucher_loaded_at
from gl
inner join vouchers
    on vouchers.voucher_no = gl.voucher_no
