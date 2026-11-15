{% docs __overview__ %}
# Regnskap lakehouse

A fake Norwegian ERP (accounting system) loaded by dlt into `lakehouse_dev.bronze`, and modelled with
dbt into silver (cleaned, typed, deduplicated, SCD2 history) and gold (trial balance, aged accounts
receivable, customer summary).

- **Bronze** = what we received. **Silver** = what is true. **Gold** = what it means.
- Accounting controls run as `warn` tests tagged `audit`. Failing rows are stored in `lakehouse_dev.audit`.
- Start in the lineage graph from `exposure:revisjonsrapport` and follow it back to the sources.
{% enddocs %}

{% docs bilag %}
**Bilag (voucher).** One accounting transaction with one voucher number and two or more lines.
A voucher must always balance: the sum of debits = the sum of credits.
{% enddocs %}

{% docs hovedbok %}
**Hovedbok (general ledger).** All posted voucher lines, per account. The book of record that every
report is built from.
{% enddocs %}

{% docs saldobalanse %}
**Saldobalanse (trial balance).** The balance of every account per period: opening balance,
debit, credit and closing balance. Across all accounts it must net to zero. Balance sheet accounts
(class 1-2) carry their balance forward. P&L accounts (class 3-8) start from zero each financial year.
{% enddocs %}

{% docs kundereskontro %}
**Kundereskontro (accounts receivable sub-ledger).** What each customer owes, per invoice. Here it is
built from the general ledger (account 1500), so it can be reconciled against it.
{% enddocs %}

{% docs aldersfordeling %}
**Aldersfordelt kundereskontro (aged receivables).** Open invoices grouped by days past due:
`not_due`, `1-30`, `31-60`, `61-90`, `90+`, measured on `as_of_date`.
{% enddocs %}

{% docs kid %}
**KID (kundeidentifikasjon).** The Norwegian payment reference printed on the invoice, with a mod 10
check digit. It lets the bank payment be matched automatically to the right invoice.
{% enddocs %}

{% docs ns4102 %}
**NS 4102.** The Norwegian standard chart of accounts. The first digit is the account class:
1 assets, 2 equity and liabilities, 3 revenue, 4 cost of goods, 5 payroll, 6-7 other operating
expenses, 8 financial items. Examples: 1500 kundefordringer (AR), 1920 bank, 2400 leverandørgjeld (AP),
2700/2710 VAT.
{% enddocs %}

{% docs regnskapsperiode %}
**Regnskapsperiode (accounting period).** A month (`yyyy-MM`). When a period is closed (after VAT
reporting or year-end), no new postings may be dated in it. See the seed `accounting_periods`.
{% enddocs %}
