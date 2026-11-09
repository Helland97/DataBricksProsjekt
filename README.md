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

## Opprydding

Alt skal kunne rives med `terraform destroy` fra `infra/`. Sjekk kostnadsoversikten i Azure etterpå.
