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
uses the Container Apps Job's system-assigned managed identity by default in
the Azure deployment instructions below. The identity needs Blob data access
to read the PDF and update its metadata. Local runs can use Azure CLI
credentials. Connection-string mode remains available for existing deployments.
No SAS token is required, and URL query parameters are ignored.

The job prints one JSON response to stdout and structured progress logs to
stderr. An execution is unsuccessful if downloading, scanning all pages, or
updating blob metadata fails. If some pages scan successfully and others fail,
the job records the barcodes it found and returns a `partial` result.

## Local development

Sign in to Azure CLI and make sure your account has `Storage Blob Data
Contributor` on the source container. Then select the Azure CLI credential
mode:

```powershell
az login
$env:AZURE_STORAGE_AUTH_MODE = "azure_cli"
$env:AZURE_STORAGE_ACCOUNT_URL = "https://alinkdev.blob.core.windows.net"
& .\venv\Scripts\python.exe job.py "https://<account>.blob.core.windows.net/<container>/<file>.pdf"
```

For existing deployments that still use a connection string, leave
`AZURE_STORAGE_AUTH_MODE` unset and set `AZURE_STORAGE_CONNECTION_STRING`
instead. Do not commit a real `.env` file or put credentials in source control.

For a local input PDF, set `LOCAL_INPUT` and leave `INPUT_PDF_URL` unset. The
local mode updates that PDF in place. Set `LOCAL_OUTPUT_DIR` if you also want a
local JSON copy of the result.

Run the tests with:

```powershell
& .\venv\Scripts\python.exe -m pip install -r requirements.txt
& .\venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Build and deploy an Azure Container Apps Job

These commands use PowerShell, Azure CLI, Azure Container Registry (ACR), and an
existing Azure subscription. This example configures a system-assigned Job
identity for Blob access and a user-assigned identity for ACR image pulls.
Choose a globally unique lowercase ACR name.

```powershell
$ErrorActionPreference = "Stop"
$resourceGroup = "rg-barcode-scanner"
$location = "centralindia"
$environmentName = "cae-barcode-scanner"
$registryName = "<globally-unique-lowercase-acr-name>"
$jobName = "barcode-pdf-job"
$identityName = "id-barcode-pdf-job"
$image = "$registryName.azurecr.io/barcode-scanner:1"
$storageAccountName = "alinkdev"
$storageContainerName = "barcodescanner"
$storageResourceGroup = "<storage account resource group>"
$storageAccountUrl = "https://$storageAccountName.blob.core.windows.net"

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
  --env-vars `
    "AZURE_STORAGE_AUTH_MODE=managed_identity" `
    "AZURE_STORAGE_ACCOUNT_URL=$storageAccountUrl" `
    "SCAN_DPIS=150,170,200,250" `
    "WORKERS=2"

# Keep the user-assigned identity for ACR pulls and add a system identity for Blob access.
az containerapp job identity assign `
  --name $jobName `
  --resource-group $resourceGroup `
  --system-assigned `
  --user-assigned $identityId
$jobPrincipalId = az containerapp job show `
  --name $jobName `
  --resource-group $resourceGroup `
  --query identity.principalId `
  --output tsv
$storageAccountId = az storage account show `
  --name $storageAccountName `
  --resource-group $storageResourceGroup `
  --query id `
  --output tsv
$containerScope = "$storageAccountId/blobServices/default/containers/$storageContainerName"
Write-Host "Ask an administrator to grant Storage Blob Data Contributor to $jobPrincipalId at $containerScope"
# An administrator with role-assignment permissions can run:
# az role assignment create --assignee-object-id $jobPrincipalId `
#   --assignee-principal-type ServicePrincipal `
#   --role "Storage Blob Data Contributor" --scope $containerScope
```

Run one PDF by setting the URL on the job and then starting it:

```powershell
$pdfUrl = "https://<account>.blob.core.windows.net/<container>/<file>.pdf"
# This shared-template update is for sequential manual testing only.
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
$registryIdentityName = "schomp-dev-bg-service"
$storageAccountName = "alinkdev"
$storageResourceGroup = "alinkdev-res-grp"
$containerName = "barcodescanner"
$subscriptionId = "d3921e5f-f495-4283-909f-d29068ff2d07"
$imageTag = Get-Date -Format "yyyyMMdd-HHmmss"
$image = "$registryName.azurecr.io/barcode-scanner:$imageTag"

az account set --subscription $subscriptionId
if ($LASTEXITCODE -ne 0) {
  throw "Could not select the Schomp subscription"
}
az extension add --name containerapp --upgrade

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
  --set-env-vars `
    "AZURE_STORAGE_AUTH_MODE=managed_identity" `
    "AZURE_STORAGE_ACCOUNT_URL=https://$storageAccountName.blob.core.windows.net" `
    "WORKERS=2" `
    "SCAN_DPIS=150,170,200,250"
if ($LASTEXITCODE -ne 0) {
  throw "Container Apps Job update failed"
}

$registryIdentityId = az identity show `
  --name $registryIdentityName `
  --resource-group $resourceGroup `
  --query id `
  --output tsv
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($registryIdentityId)) {
  throw "Could not find the existing ACR-pull identity"
}
az containerapp job identity assign `
  --name $jobName `
  --resource-group $resourceGroup `
  --system-assigned `
  --user-assigned $registryIdentityId
if ($LASTEXITCODE -ne 0) {
  throw "Could not assign the Job managed identities"
}

$jobPrincipalId = az containerapp job show `
  --name $jobName `
  --resource-group $resourceGroup `
  --query identity.principalId `
  --output tsv
$storageAccountId = az storage account show `
  --name $storageAccountName `
  --resource-group $storageResourceGroup `
  --query id `
  --output tsv
if (
  $LASTEXITCODE -ne 0 -or
  [string]::IsNullOrWhiteSpace($jobPrincipalId) -or
  [string]::IsNullOrWhiteSpace($storageAccountId)
) {
  throw "Could not resolve the Job identity or storage account"
}
$containerScope = "$storageAccountId/blobServices/default/containers/$containerName"
Write-Host "Ask an administrator to grant Storage Blob Data Contributor to $jobPrincipalId at $containerScope"
```

An administrator with role-assignment permissions must grant the Job identity
`Storage Blob Data Contributor` at `$containerScope`. The user-assigned identity
continues to pull the image from ACR.

The administrator can run this after receiving `$jobPrincipalId` and
`$containerScope`:

```powershell
az role assignment create `
  --assignee-object-id "<jobPrincipalId>" `
  --assignee-principal-type ServicePrincipal `
  --role "Storage Blob Data Contributor" `
  --scope "<containerScope>"
```

### Test the deployed job without a Logic App

After the new image is deployed and the Job identity has Blob access, use `az
rest` to make the same ARM Start call the Logic App will make. Your signed-in
account needs permission to start the Job, but it does not need Blob access:

```powershell
$subscriptionId = az account show --query id --output tsv
$pdfUrl = "https://alinkdev.blob.core.windows.net/barcodescanner/test2.pdf"
$startUrl = "https://management.azure.com/subscriptions/$subscriptionId/resourceGroups/$resourceGroup/providers/Microsoft.App/jobs/$jobName/start?api-version=2026-07-01"
$body = @{
  containers = @(
    @{
      name = "alynk-filebunker-dev-cont-job"
      image = $image
      command = @("python", "/app/job.py")
      args = @($pdfUrl)
      resources = @{ cpu = 2; memory = "4Gi" }
      env = @(
        @{ name = "AZURE_STORAGE_AUTH_MODE"; value = "managed_identity" },
        @{ name = "AZURE_STORAGE_ACCOUNT_URL"; value = "https://alinkdev.blob.core.windows.net" },
        @{ name = "WORKERS"; value = "2" },
        @{ name = "SCAN_DPIS"; value = "150,170,200,250" }
      )
    }
  )
} | ConvertTo-Json -Depth 8

$startResult = az rest `
  --method post `
  --url $startUrl `
  --headers "Content-Type=application/json" `
  --body $body
if ($LASTEXITCODE -ne 0) {
  throw "Container Apps Job start request failed"
}
$startResult
```

Check **Container Apps Jobs → `alynk-filebunker-dev-cont-job` → Executions**
and inspect the execution logs. Verify `BarcodeResults` in the blob metadata.
The container override must include the container name, image, resources,
command, and environment values; the `args` array carries the PDF URL.

### Start this job from a Logic App

Use the Container Apps Jobs **Start** REST operation to start one execution per
PDF. Unlike changing `INPUT_PDF_URL` on the shared job and then starting it,
the per-execution container override passes the URL without racing other runs.
The job image's entry point is `python /app/job.py`; its positional argument is
the PDF URL. Do not use `BLOB_URL`—the job does not read that variable.

Configure an HTTP action:

- Method: `POST`
- URI:
  `https://management.azure.com/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.App/jobs/<job-name>/start?api-version=2026-07-01`
- Authentication: **Managed identity**, using the Logic App's system-assigned
  identity
- Audience: `https://management.azure.com/`
- Request body: provide the execution container override, including the
  container name, image, resources, command, and the environment settings the
  job needs. Use the Blob URL dynamic value from your trigger/action in `args`.
  Update the image tag if you deploy a newer image.

Before the first Logic App run, enable the job's system-assigned identity while
retaining its existing user-assigned ACR-pull identity, then have an Azure
administrator grant the job identity Blob access:

```powershell
$resourceGroup = "alinkdev-res-grp"
$jobName = "alynk-filebunker-dev-cont-job"
$registryIdentityName = "schomp-dev-bg-service"
$storageResourceGroup = "alinkdev-res-grp"
$storageAccountName = "alinkdev"
$containerName = "barcodescanner"

$registryIdentityId = az identity show `
  --name $registryIdentityName `
  --resource-group $resourceGroup `
  --query id `
  --output tsv
az containerapp job identity assign `
  --name $jobName `
  --resource-group $resourceGroup `
  --system-assigned `
  --user-assigned $registryIdentityId

$jobPrincipalId = az containerapp job show `
  --name $jobName `
  --resource-group $resourceGroup `
  --query identity.principalId `
  --output tsv
$storageAccountId = az storage account show `
  --name $storageAccountName `
  --resource-group $storageResourceGroup `
  --query id `
  --output tsv
$containerScope = "$storageAccountId/blobServices/default/containers/$containerName"
```

Ask an administrator with role-assignment permissions to run:

```powershell
az role assignment create `
  --assignee-object-id "<jobPrincipalId from above>" `
  --assignee-principal-type ServicePrincipal `
  --role "Storage Blob Data Contributor" `
  --scope "<containerScope from above>"
```

Example body (replace the image tag and Logic App expression with your values):

```json
{
  "containers": [
    {
      "name": "alynk-filebunker-dev-cont-job",
      "image": "alynkfilebunkerregistry.azurecr.io/barcode-scanner:<image-tag>",
      "command": ["python", "/app/job.py"],
      "args": ["@{outputs('Compose_BlobUrl')}"],
      "resources": {
        "cpu": 2,
        "memory": "4Gi"
      },
      "env": [
        {
          "name": "AZURE_STORAGE_AUTH_MODE",
          "value": "managed_identity"
        },
        {
          "name": "AZURE_STORAGE_ACCOUNT_URL",
          "value": "https://alinkdev.blob.core.windows.net"
        },
        {
          "name": "WORKERS",
          "value": "2"
        },
        {
          "name": "SCAN_DPIS",
          "value": "150,170,200,250"
        }
      ]
    }
  ]
}
```

The Logic App identity needs permission to start the job. The built-in
`Container Apps Jobs Operator` role can start jobs, but review its permissions
before assigning it: operator permissions can include access to job secrets.
Prefer a job-scoped custom role that allows
`Microsoft.App/jobs/start/action` and only the read actions required by your
workflow. The job's separate system-assigned identity needs
`Storage Blob Data Contributor` on the source container; the existing
user-assigned identity can remain attached for ACR image pulls. The Logic App
identity does not need Blob permissions.

The start response includes an execution ID. Use the Jobs execution
`GET` operation if the workflow needs to poll completion; the API's start
response alone only confirms that Azure accepted/started the execution.

### Adjustments and operations

- Set `WORKERS` to fit the job's CPU and memory allocation. The recommended
  starting point is 2 CPU, 4 GiB memory, and 2 workers. Container Apps Job
  executions use all allocated CPUs by default; local runs leave one CPU free.
  Benchmark with representative PDFs before increasing the worker count further.
- Increase `--replica-timeout` for large or multi-page PDFs.
- Use a new image tag, then update the job image when deploying code changes.
- The job uses its system-assigned identity for Blob Storage. Keep the
  user-assigned identity attached for private ACR image pulls.
- Existing connection-string mode is retained for legacy runs; prefer managed
  identity and remove any obsolete storage connection-string secret after
  validating the new path.
- The metadata update preserves other existing blob metadata and uses the
  original ETag to avoid applying results if the blob changed during scanning.

## References

- [Azure Container Apps Jobs: create and run a manual job](https://learn.microsoft.com/en-us/azure/container-apps/jobs-get-started-cli)
- [Azure Container Apps Jobs: Start REST API](https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/jobs/start?view=rest-resource-manager-containerapps-2026-07-01)
- [Azure CLI: manage Container Apps Job identities](https://learn.microsoft.com/en-us/cli/azure/containerapp/job/identity?view=azure-cli-latest)
- [Azure Container Apps jobs vs. continuously running apps](https://learn.microsoft.com/en-us/azure/container-apps/jobs)
- [Azure CLI `az containerapp job` reference](https://learn.microsoft.com/en-us/cli/azure/containerapp/job?view=azure-cli-latest)
- [Use a managed identity to pull images from ACR](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity-image-pull)
- [Download blobs with the Azure Storage Python SDK](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-download-python)
- [Upload blobs with the Azure Storage Python SDK](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-upload-python)
- [Authorize access to blobs with Microsoft Entra ID](https://learn.microsoft.com/en-us/azure/storage/blobs/authorize-access-azure-active-directory)
- [Azure Identity Python credential classes](https://learn.microsoft.com/en-us/python/api/overview/azure/identity-readme?view=azure-python)
- [Azure CLI `az containerapp identity` reference](https://learn.microsoft.com/en-us/cli/azure/containerapp/identity?view=azure-cli-latest)
- [Azure Storage Python `BlobClient` conditional operations](https://learn.microsoft.com/en-us/python/api/azure-storage-blob/azure.storage.blob.blobclient?view=azure-python)
- [Azure Blob metadata](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-properties-metadata)
- [Configure Logic Apps managed identity authentication](https://learn.microsoft.com/en-us/azure/logic-apps/authenticate-with-managed-identity)
- [Azure built-in roles for Containers](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/containers)
