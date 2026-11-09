# Bootstrap: remote state for Terraform

Terraform-state for `01-azure` og `02-databricks` lagres i en egen Azure Storage-konto. Den kontoen
kan ikke lages av de samme Terraform-modulene (høna og egget), så den opprettes én gang manuelt med
`az` og rives ikke sammen med resten.

Navnene under må stemme med `backend.tf` i begge modulene og med `data "terraform_remote_state"` i
`02-databricks/main.tf`. Storage-kontonavn er globalt unike (3–24 tegn, kun små bokstaver og tall).
Er `sttfstatech4821` tatt, velg et annet og oppdater de tre stedene.

Kommandoene er skrevet for **PowerShell** (Windows). Variablene lever bare i det åpne
terminalvinduet, så kjør steg 1 på nytt hvis du åpner et nytt vindu.

> Faktisk oppsett: resource group `rg-tfstate` ligger i `westeurope`, mens storage-kontoen
> `sttfstatech4821` ligger i `northeurope`. Det fungerer fint (en resource group er bare metadata),
> men ved et nytt oppsett bør begge ligge i samme region som resten av prosjektet.

## 1. Logg inn og sett variabler

```powershell
az login
az account set --subscription "<subscription-id>"

$LOCATION        = "northeurope"
$STATE_RG        = "rg-tfstate"
$STATE_SA        = "sttfstatech4821"
$STATE_CONTAINER = "tfstate"
```

## 2. Opprett resource group og storage-konto for state

```powershell
az group create --name $STATE_RG --location $LOCATION

az storage account create `
  --name $STATE_SA `
  --resource-group $STATE_RG `
  --location $LOCATION `
  --sku Standard_LRS `
  --kind StorageV2 `
  --min-tls-version TLS1_2 `
  --allow-blob-public-access false `
  --allow-shared-key-access false

# Versjonering + soft delete gjør det mulig å hente tilbake en ødelagt state-fil
az storage account blob-service-properties update `
  --account-name $STATE_SA `
  --resource-group $STATE_RG `
  --enable-versioning true `
  --enable-delete-retention true --delete-retention-days 7
```

## 3. Gi deg selv dataplan-tilgang og lag containeren

Backendene bruker Entra ID (`use_azuread_auth = true`), ikke storage-nøkler. *Owner* på
subscriptionen gir bare kontrollplan-tilgang (opprette/endre ressurser), ikke tilgang til innholdet.
Brukeren (og senere CI-identiteten) trenger derfor rollen *Storage Blob Data Contributor* på kontoen:

```powershell
$SA_ID = az storage account show --name $STATE_SA --resource-group $STATE_RG --query id -o tsv
$ME    = az ad signed-in-user show --query id -o tsv

az role assignment create `
  --assignee-object-id $ME --assignee-principal-type User `
  --role "Storage Blob Data Contributor" `
  --scope $SA_ID

az storage container create `
  --name $STATE_CONTAINER `
  --account-name $STATE_SA `
  --auth-mode login
```

Rolletildelinger kan bruke et par minutter på å slå gjennom. Feiler `container create` med 403,
vent litt og prøv igjen. Sjekk at tilgangen virker (tom liste, ingen feil):

```powershell
az storage blob list --account-name $STATE_SA -c $STATE_CONTAINER --auth-mode login -o table
```

## 4. Kjør modulene i rekkefølge

```powershell
cd infra/01-azure
Copy-Item terraform.example.tfvars terraform.tfvars   # fyll inn subscription_id
terraform init
terraform plan -out tfplan
terraform apply tfplan

cd ../02-databricks
# Repoet er privat, så Databricks trenger en GitHub-token (fine-grained PAT, Contents: Read,
# kun dette repoet). Sett den som miljøvariabel, aldri i en fil:
$env:TF_VAR_github_token = "<github-pat>"
terraform init
terraform plan -out tfplan
terraform apply tfplan
```

`02-databricks` trenger ingen `terraform.tfvars`; alle variabler har standardverdier, og
workspace-URL og storage-detaljer hentes fra `01-azure` via `terraform_remote_state`.

Riving skjer i motsatt rekkefølge: `terraform destroy` i `02-databricks` først, så i `01-azure`.
State-kontoen fra dette steget slettes til slutt manuelt:

```powershell
az group delete --name rg-tfstate
```
