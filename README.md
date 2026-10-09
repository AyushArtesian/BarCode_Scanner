# Barcode PDF scanner

This project scans a PDF from Azure Blob Storage and writes the detected
barcodes into the source blob's `BarcodeResults` metadata field as compact JSON,
for example `[{"page":1,"value":"ABC123"}]`. The PDF content is not rewritten.
If no barcode is detected, the field is set to an empty JSON array (`[]`).
Local-file runs still write `/BarcodeResults` to the PDF's document metadata.

## Azure Container Apps Job input

Pass one HTTPS Azure Blob URL for each execution. For example:

```text
https://<account>.blob.core.windows.net/<container>/<path>/<file>.pdf
```

The URL identifies the storage account, container, and blob. Authentication
comes from `AZURE_STORAGE_CONNECTION_STRING`; no SAS token is required. The
connection string must refer to the same storage account as the URL and have
permission to read the blob and set its metadata. URL query parameters are
ignored.

The job prints one JSON response to stdout and structured progress logs to
stderr. An execution is unsuccessful if downloading, scanning all pages, or
updating blob metadata fails. If some pages scan successfully and others fail,
the job records the barcodes it found and returns a `partial` result.

## Local development

Install the requirements, then set `AZURE_STORAGE_CONNECTION_STRING` in your
shell or local secret store. Do not commit a real `.env` file or put a
connection string in source control.

```powershell
$env:AZURE_STORAGE_CONNECTION_STRING = "<storage connection string>"
& .\venv\Scripts\python.exe job.py "https://<account>.blob.core.windows.net/<container>/<file>.pdf"
```

For a local input PDF, set `LOCAL_INPUT` and leave `INPUT_PDF_URL` unset. The
local mode updates that PDF in place. Set `LOCAL_OUTPUT_DIR` if you also want a
local JSON copy of the result.

Run the tests with:

```powershell
& .\venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Build and deploy an Azure Container Apps Job

These commands use PowerShell, Azure CLI, Azure Container Registry (ACR), and an
existing Azure subscription. Choose a globally unique lowercase ACR name. Make
sure `AZURE_STORAGE_CONNECTION_STRING` is populated in the deployment shell
from a secure source before creating the job; never paste the actual value into
this file or a checked-in script.

```powershell
$ErrorActionPreference = "Stop"
$resourceGroup = "rg-barcode-scanner"
$location = "centralindia"
$environmentName = "cae-barcode-scanner"
$registryName = "<globally-unique-lowercase-acr-name>"
$jobName = "barcode-pdf-job"
$identityName = "id-barcode-pdf-job"
$image = "$registryName.azurecr.io/barcode-scanner:1"
if ([string]::IsNullOrWhiteSpace($env:AZURE_STORAGE_CONNECTION_STRING)) {
  throw "Set AZURE_STORAGE_CONNECTION_STRING from a secure source before continuing."
}

az login
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
az provider register --namespace Microsoft.OperationalInsights

az group create --name $resourceGroup --location $location
az acr create --resource-group $resourceGroup --name $registryName --sku Basic

# Build from this project directory; the .dockerignore excludes local PDFs and .env files.
az acr build --registry $registryName --image barcode-scanner:1 .

az containerapp env create `
  --name $environmentName `
  --resource-group $resourceGroup `
  --location $location

# Managed identity image pulls require ARM-audience tokens to be enabled on ACR.
az acr config authentication-as-arm update `
  --name $registryName `
  --status enabled

# Create and authorize a user-assigned identity before creating the job with a private image.
az identity create `
  --name $identityName `
  --resource-group $resourceGroup `
  --location $location
$identityId = az identity show `
  --name $identityName `
  --resource-group $resourceGroup `
  --query id `
  --output tsv
$identityPrincipalId = az identity show `
  --name $identityName `
  --resource-group $resourceGroup `
  --query principalId `
  --output tsv
$registryId = az acr show `
  --name $registryName `
  --resource-group $resourceGroup `
  --query id `
  --output tsv
az role assignment create `
  --assignee-object-id $identityPrincipalId `
  --assignee-principal-type ServicePrincipal `
  --role AcrPull `
  --scope $registryId

az containerapp job create `
  --name $jobName `
  --resource-group $resourceGroup `
  --environment $environmentName `
  --trigger-type Manual `
  --replica-timeout 1800 `
  --replica-retry-limit 1 `
  --replica-completion-count 1 `
  --parallelism 1 `
  --image $image `
  --cpu 2 `
  --memory 4Gi `
  --mi-user-assigned $identityId `
  --registry-server "$registryName.azurecr.io" `
  --registry-identity $identityId `
  --secrets "blob-connection=$env:AZURE_STORAGE_CONNECTION_STRING" `
  --env-vars `
    "AZURE_STORAGE_CONNECTION_STRING=secretref:blob-connection" `
    "SCAN_DPIS=150,170,200,250" `
    "WORKERS=2"
```

Run one PDF by setting the URL on the job and then starting it:

```powershell
$pdfUrl = "https://<account>.blob.core.windows.net/<container>/<file>.pdf"
az containerapp job update `
  --name $jobName `
  --resource-group $resourceGroup `
  --set-env-vars "INPUT_PDF_URL=$pdfUrl"
az containerapp job start `
  --name $jobName `
  --resource-group $resourceGroup
```

### Deploy an updated image to the existing job

From the project root, build a uniquely tagged image and update the existing
job. The 2 CPU / 4 GiB allocation and two workers are a starting point for
improving scan throughput; Azure executions default to using all allocated
CPUs if `WORKERS` is not set.

```powershell
$registryName = "alynkfilebunkerregistry"
$resourceGroup = "alinkdev-res-grp"
$jobName = "alynk-filebunker-dev-cont-job"
$imageTag = Get-Date -Format "yyyyMMdd-HHmmss"
$image = "$registryName.azurecr.io/barcode-scanner:$imageTag"

az acr build `
  --registry $registryName `
  --image "barcode-scanner:$imageTag" `
  .
if ($LASTEXITCODE -ne 0) {
  throw "Container image build failed"
}

az containerapp job update `
  --resource-group $resourceGroup `
  --name $jobName `
  --image $image `
  --cpu 2 `
  --memory 4Gi `
  --set-env-vars "WORKERS=2"
if ($LASTEXITCODE -ne 0) {
  throw "Container Apps Job update failed"
}
```

Then set the PDF URL and start an execution:

```powershell
$pdfUrl = "https://alinkdev.blob.core.windows.net/barcodescanner/test2.pdf"
az containerapp job update `
  --resource-group $resourceGroup `
  --name $jobName `
  --set-env-vars "INPUT_PDF_URL=$pdfUrl"
az containerapp job start `
  --resource-group $resourceGroup `
  --name $jobName
```

In the Azure portal, open **Container Apps Jobs → barcode-pdf-job → Executions**
to confirm completion and inspect replica logs when logging is configured.
Open the blob in the Storage account and check **Overview → Metadata** for
`BarcodeResults`. The PDF's contents remain unchanged.

### Adjustments and operations

- Set `WORKERS` to fit the job's CPU and memory allocation. The recommended
  starting point is 2 CPU, 4 GiB memory, and 2 workers. Container Apps Job
  executions use all allocated CPUs by default; local runs leave one CPU free.
  Benchmark with representative PDFs before increasing the worker count further.
- Increase `--replica-timeout` for large or multi-page PDFs.
- Use a new image tag, then update the job image when deploying code changes.
- The storage connection string is stored as a Container Apps secret and
  exposed to the container through a `secretref`. Do not set it as a plain
  environment variable in the job definition.
- The metadata update preserves other existing blob metadata and uses the
  original ETag to avoid applying results if the blob changed during scanning.

## References

- [Azure Container Apps Jobs: create and run a manual job](https://learn.microsoft.com/en-us/azure/container-apps/jobs-get-started-cli)
- [Azure CLI `az containerapp job` reference](https://learn.microsoft.com/en-us/cli/azure/containerapp/job?view=azure-cli-latest)
- [Use a managed identity to pull images from ACR](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity-image-pull)
- [Download blobs with the Azure Storage Python SDK](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-download-python)
- [Upload blobs with the Azure Storage Python SDK](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-upload-python)
- [Azure Storage Python `BlobClient` conditional operations](https://learn.microsoft.com/en-us/python/api/azure-storage-blob/azure.storage.blob.blobclient?view=azure-python)
- [Azure Blob metadata](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-properties-metadata)
