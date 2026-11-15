{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='entry_id',
    on_schema_change='append_new_columns'
) }}

with source as (
    select * from {{ source('erp', 'gl_entries') }}
    {% if is_incremental() %}
    -- Filter on ARRIVAL, not on updated_at. _dlt_load_id is set by the loader and only ever increases,
    -- so late lines (old updated_at, new load) are picked up too.
    where {{ dlt_loaded_at('_dlt_load_id') }} > (select max(loaded_at) from {{ this }})
    {% endif %}
),

renamed as (
    select
        entry_id,
        voucher_no,
        cast(line_no as int)                 as line_no,
        cast(entry_date as date)             as entry_date,
        cast(account_no as int)              as account_no,
        trim(description)                    as description,
        cast(debit as decimal(18, 2))        as debit,
        cast(credit as decimal(18, 2))       as credit,
        invoice_id,
        customer_id,
        updated_at                           as posted_at,
        _dlt_load_id,
        {{ dlt_loaded_at('_dlt_load_id') }}  as loaded_at
    from source
)

select * from renamed
qualify row_number() over (
    partition by entry_id
    order by posted_at desc, _dlt_load_id desc
) = 1
