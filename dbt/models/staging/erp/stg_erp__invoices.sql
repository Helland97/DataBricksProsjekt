with source as (
    select * from {{ source('erp', 'invoices') }}
),

typed as (
    select
        invoice_id,
        customer_id,
        cast(invoice_date as date)                         as invoice_date,
        cast(due_date as date)                             as due_date,
        cast(amount_ex_vat as decimal(18, 2))              as amount_ex_vat,
        cast(vat_amount as decimal(18, 2))                 as vat_amount_source,
        coalesce(
            cast(amount_incl_vat as decimal(18, 2)),
            {{ parse_nok_amount('amount_incl_vat__v_text') }}
        )                                                  as amount_incl_vat,
        amount_incl_vat is null
            and amount_incl_vat__v_text is not null        as is_amount_parsed_from_text,
        lower(trim(status))                                as status,
        coalesce(upper(trim(currency)), 'NOK')             as currency,
        nullif(trim(kid), '')                              as kid,
        updated_at,
        _dlt_id,
        _dlt_load_id,
        {{ dlt_loaded_at('_dlt_load_id') }}                as loaded_at
    from source
),

final as (
    select
        * except (vat_amount_source),
        coalesce(vat_amount_source, amount_incl_vat - amount_ex_vat) as vat_amount,
        vat_amount_source is null                                    as is_vat_derived
    from typed
)

select * from final
qualify row_number() over (
    partition by invoice_id
    order by updated_at desc, _dlt_load_id desc
) = 1
