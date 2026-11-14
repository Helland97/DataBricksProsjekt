with source as (
    select * from {{ source('erp', 'accounts') }}
),

renamed as (
    select
        cast(account_no as int)          as account_no,
        trim(name)                       as account_name,
        lower(trim(account_type))        as account_type,
        cast(account_class as int)       as account_class,
        nullif(trim(vat_code), '')       as vat_code,
        is_active,
        _dlt_load_id,
        {{ dlt_loaded_at('_dlt_load_id') }} as loaded_at
    from source
)

select * from renamed
