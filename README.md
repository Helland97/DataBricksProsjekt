# DataBricksProsjekt

Hands-on lakehouse-prosjekt på Azure Databricks: Terraform-infrastruktur, ingest med dlt til bronze,
modellering med dbt core (silver/gold), Unity Catalog-tilgangsstyring, orkestrering med Databricks
Workflows og CI/CD i GitHub Actions.

All kode ligger i dette repoet. Databricks kobles til repoet (Git folder / Git-kilde i jobber), så koden
hentes herfra og kopieres ikke inn manuelt.

## Struktur

| Mappe            | Innhold                                                                 |
|------------------|-------------------------------------------------------------------------|
| `infra/`         | Terraform (azurerm + databricks provider): RG, ADLS Gen2, workspace, UC  |
| `ingestion/`     | Syntetiske data (Faker), fake kilde og dlt-pipelines til bronze          |
| `dbt/`           | dbt core-prosjekt: silver og gold, snapshots, tester, docs               |
| `orchestration/` | Databricks Workflows/Jobs-definisjoner (og ev. lokal Airflow som bonus)  |
| `ci/`            | Hjelpeskript for CI. Selve GitHub Actions-workflows ligger i `.github/workflows/` |

## Tilgangsstyring: roller

> **Status: planlagt, ikke implementert.** Steg 4 ble blokkert av begrensninger i Azure (se
> [Kjente begrensninger](#kjente-begrensninger)). Seksjonen beskriver designet slik det er planlagt i
> `STEG4-PLAN.md`. Gruppene, `sp-dbt` og `sp-test-*` finnes ikke ennå.

Tilgang gis til **grupper**, aldri til enkeltpersoner. Hver rolle er en gruppe på kontonivå i Databricks
(account group), definert i Terraform i `infra/02-databricks/`. Hva hver gruppe faktisk får lese og skrive,
står i grant-matrisen (steg 4, seksjon 3). Denne seksjonen beskriver *hvem* som finnes og *hvorfor*.

### Rollene

| Gruppe | Hvem | Trenger | Skal ikke |
|---|---|---|---|
| `dbx_pipeline` | Tjenestekontoen `sp-dbt` som jobben kjører som, og utvikleren som kjører dbt lokalt | Skrive til bronze, silver, gold, audit og governance | Ha menneskelige medlemmer i prod |
| `dbx_engineers` | Dataingeniører | Lese alle lag for å feilsøke, og kjøre og følge med på jobber | Endre gold for hånd, eller se personopplysninger i klartekst |
| `dbx_analysts` | Controllere og analytikere | Lese gold for alle kunder | Se bronze og silver, eller se e-post og adresse |
| `dbx_auditors` | Revisor (intern eller ekstern) | Lese silver, gold, audit, historikk og lineage, med personopplysninger i klartekst | Endre noe som helst, noe sted |
| `kunde_<id>` | Én gruppe per kunde (demo: `kunde_10001`, `kunde_10002`) | Se sine egne rader i kundeportalen | Se andre kunders data eller interne tabeller |

**Hva «kunde» betyr her:** kundene i ERP-dataene (`customer_id` 10001, 10002, ...). Scenarioet er en
kundeportal der hver kunde kan logge inn og se sine egne fakturaer, åpne poster og sin egen oppsummering. Hvis
«kunde» i stedet betyr et revisjonsklient-selskap med eget ERP, er mekanismen den samme. Bare nøkkelen
filteret bruker endres, fra `customer_id` til for eksempel `company_id`.

### Prinsippene

1. **Grupper, ikke personer.** En person meldes inn i eller ut av en gruppe. Ingen grant nevner en bruker ved
   navn. Da blir «hvem har tilgang til X?» et spørsmål om gruppemedlemskap, og når noen slutter, fjernes de fra
   én gruppe i stedet for fra grants spredt over mange tabeller.
2. **Bare pipelinen skriver.** Mennesker leser. En endring i gold går via Git og jobben, aldri via en `UPDATE`
   i en notebook. Det er det som gjør tallene og Delta-historikken troverdige.
3. **Jobben kjører som en tjenestekonto (service principal), ikke som en person.** Kjører jobben som den som
   opprettet den, får den alle den personens rettigheter. Som `sp-dbt` kan den gjøre akkurat det
   `dbx_pipeline` kan, og historikken viser `sp-dbt` som den som skrev.
4. **Kontogrupper, ikke workspace-grupper.** Unity Catalog forstår bare grupper på kontonivå. Workspace-lokale
   grupper ser like ut i grensesnittet, men kan ikke brukes i grants. Row filters sjekker derfor
   `is_account_group_member()`, ikke `is_member()`.
5. **Standard er nei.** Uten grant er det ingen tilgang, og en gruppe som ikke er nevnt i et row filter, ser
   ingen rader. En gruppe man har glemt, ser altså for lite, ikke for mye.

### Kjente kompromisser (bare i dev)

- **Utvikleren er med i `dbx_pipeline`.** dbt kjøres også lokalt, og pipelinen må se ufiltrerte og umaskerte
  data for å bygge gold riktig. I prod er ingen mennesker med i denne gruppen. Der kjører bare jobben, som `sp-dbt`.
- **Test-tjenestekontoer.** Hver rolle har en `sp-test-*`-konto, slik at det kan testes automatisk at for
  eksempel en kunde ikke ser andre kunders data. Det kan ikke bevises når man er logget inn som seg selv, siden man
  da ser alt. Kontoenes hemmeligheter fjernes når testingen er ferdig.

## CI-identitet

GitHub Actions logger inn som Entra-appen **`gh-dbxprosjekt-ci`** med **OIDC** (federated credentials). GitHub
utsteder et kortlivet token per kjøring, og Entra stoler bare på tokens fra dette repoet. Det finnes **ingen
lagrede hemmeligheter**: appen har ingen client secret og intet sertifikat. Appen er laget for hånd med `az`
(`STEG5-PLAN.md`, seksjon 2.1), fordi Terraform-modulene selv trenger en identitet for å kjøre.

| Hvor | Rettighet | Hvorfor |
|---|---|---|
| Entra-appen | Federated credentials for `pull_request`, `main` og environment `ci` | Bare disse kjøringene i dette repoet kan logge inn som appen |
| `rg-dbxprosjekt-dev`, `rg-tfstate` | Reader | `terraform plan` må lese alle Azure-ressurser |
| Container `tfstate` | Storage Blob Data Contributor | `plan` tar en lås (lease) på state-filen. State-kontoen tillater bare Entra-innlogging |
| Databricks-workspacet | Contributor (bare på workspace-ressursen) | Gir workspace admin ved første innlogging, uten kontokonsollet |
| Unity Catalog | Grants i `infra/02-databricks/ci.tf` | Workspace admin gir ingen UC-rettigheter. `plan` må kunne lese alle UC-objekter, og dbt-bygg på PR-er leser bronze og lager egne `pr_<n>_*`-skjemaer |

**Unity Catalog-grants i `ci.tf`:**
- Storage credential og external location: `READ_FILES`. Det finnes ingen rettighet som bare gir lesetilgang
  til metadata, og `BROWSE` er ikke nok for en external location.
- Katalogen `lakehouse_dev`: `USE_CATALOG`, `BROWSE`, `CREATE_SCHEMA`.
- Alle skjemaer: `USE_SCHEMA`. Bare `bronze` får i tillegg `SELECT` og `READ_VOLUME`. CI leser aldri silver,
  gold eller audit i prod.

CI kan kjøre `terraform plan` og `dbt build`, men **aldri `terraform apply`**. Apply gjøres lokalt av utvikleren.

## Kjente begrensninger

| Begrensning | Årsak | Konsekvens | Planlagt løsning |
|---|---|---|---|
| Ingen kontogrupper eller service principals på kontonivå | Steg 4 ble blokkert av begrensninger i Azure: Databricks-kontokonsollet (account admin) var ikke tilgjengelig | Steg 4 (grupper, row filters, kolonnemasker) er ikke implementert | Implementere steg 4 når tilgangen er på plass |
| Jobben kjører som utvikleren, ikke som en tjenestekonto | Følger av raden over | Jobben har alle utviklerens rettigheter, og Delta-historikken viser brukeren som den som skrev | `run_as` en service principal og overføre eierskapet til tabellene |
| CI har `READ_FILES` på lakens storage credential og external location | Unity Catalog har ingen rettighet som bare gir lesetilgang til metadata for disse objektene | CI kan lese rå filer i laken. Akseptabelt fordi dataene er syntetiske | Med ekte data: flytte disse objektene til en egen Terraform-modul som CI ikke kjører `plan` på |
| PR-bygg leser umaskert bronze | Ingen kolonnemasker uten steg 4 | Greit for syntetiske data, ikke for ekte personopplysninger | Med steg 4: CI-identiteten i en gruppe uten tilgang til personopplysninger i klartekst |

## Opprydding

Alt skal kunne rives med `terraform destroy`, i motsatt rekkefølge av oppsettet: først
`infra/02-databricks/`, så `infra/01-azure/`. State-kontoen fra `infra/bootstrap/` slettes til slutt
manuelt. Sjekk kostnadsoversikten i Azure etterpå.

CI-appen er ikke laget med Terraform, så den må slettes for seg. Slett rolletildelingene først. Ellers blir de
liggende igjen som «Unknown» etter at appen er borte:

```powershell
$APP = az ad app list --display-name "gh-dbxprosjekt-ci" --query "[0].appId" -o tsv
az role assignment list --assignee $APP --all --query "[].id" -o tsv | ForEach-Object { az role assignment delete --ids $_ }
az ad app delete --id $APP
```
