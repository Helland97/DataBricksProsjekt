{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='invoice_id',
    on_schema_change='append_new_columns'
) }}


{#- Columns that only exist in bronze from the day 15 schema change. Before that, add them as nulls,
    so the rest of the model (and silver) has the same columns on every day. -#}
{%- set late_columns = {'amount_incl_vat__v_text': 'string', 'currency': 'string', 'kid': 'string'} %}

with source as (
    select
        *
        {%- for column, data_type in late_columns.items() if not source_has_column('erp', 'invoices', column) %},
        cast(null as {{ data_type }}) as {{ column }}
        {%- endfor %}
    from {{ source('erp', 'invoices') }}
    {% if is_incremental() %}
    where {{ dlt_loaded_at('_dlt_load_id') }} > (select max(loaded_at) from {{ this }})
    {% endif %}
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
