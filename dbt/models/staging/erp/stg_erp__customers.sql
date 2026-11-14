with source as (
    select * from {{ source('erp', 'customers') }}
),

cleaned as (
    select
        customer_id,
        trim(name)                                       as customer_name,
        nullif(trim(org_no), '')                         as org_no,
        {{ is_valid_org_no("nullif(trim(org_no), '')") }} as is_valid_org_no,
        trim(address)                                    as address,
        trim(postal_code)                                as postal_code,
        trim(city)                                       as city,
        lower(nullif(trim(email), ''))                   as email,
        cast(payment_terms_days as int)                  as payment_terms_days,
        created_at,
        updated_at,
        _dlt_load_id,
        {{ dlt_loaded_at('_dlt_load_id') }}              as loaded_at
    from source
)

select * from cleaned
qualify row_number() over (
    partition by customer_id
    order by updated_at desc, _dlt_load_id desc
) = 1
