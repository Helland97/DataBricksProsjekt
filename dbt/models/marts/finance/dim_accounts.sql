-- SCD2 chart of accounts (kontoplan, NS 4102). One row per account version, from snp_erp__accounts.

with snapshot as (
    select * from {{ ref('snp_erp__accounts') }}
),

cleaned as (
    select
        dbt_scd_id                                          as account_sk,
        cast(account_no as int)                             as account_no,
        trim(name)                                          as account_name,
        lower(trim(account_type))                           as account_type,
        cast(account_class as int)                          as account_class,
        nullif(trim(vat_code), '')                          as vat_code,
        is_active,
        dbt_valid_from                                      as valid_from,
        dbt_valid_to                                        as valid_to,
        dbt_valid_to = cast('9999-12-31' as timestamp)      as is_current,
        row_number() over (partition by account_no order by dbt_valid_from) as version_no
    from snapshot
)

select
    *,
    case when version_no = 1 then date '1900-01-01' else cast(valid_from as date) end as valid_from_date,
    cast(valid_to as date)                                                            as valid_to_date
from cleaned
