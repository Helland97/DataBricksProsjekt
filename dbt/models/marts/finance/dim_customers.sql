-- SCD2 customer dimension (kunderegister). One row per customer version, from snp_erp__customers.
-- The snapshot stores raw values. Cleaning happens here, so it can be changed later without
-- losing history.

with snapshot as (
    select * from {{ ref('snp_erp__customers') }}
),

cleaned as (
    select
        dbt_scd_id                                          as customer_sk,
        customer_id,
        trim(name)                                          as customer_name,
        nullif(trim(org_no), '')                            as org_no,
        {{ is_valid_org_no("nullif(trim(org_no), '')") }}   as is_valid_org_no,
        trim(address)                                       as address,
        trim(postal_code)                                   as postal_code,
        trim(city)                                          as city,
        lower(nullif(trim(email), ''))                      as email,
        cast(payment_terms_days as int)                     as payment_terms_days,
        created_at,
        dbt_valid_from                                      as valid_from,
        dbt_valid_to                                        as valid_to,
        dbt_valid_to = cast('9999-12-31' as timestamp)      as is_current,
        row_number() over (partition by customer_id order by dbt_valid_from) as version_no
    from snapshot
)

select
    *,
    -- Date range for point-in-time joins on a DATE (invoice_date, entry_date): [from, to).
    -- The first version is valid from the beginning of time, so a document dated before the first
    -- snapshot still finds a version.
    case when version_no = 1 then date '1900-01-01' else cast(valid_from as date) end as valid_from_date,
    cast(valid_to as date)                                                            as valid_to_date
from cleaned
