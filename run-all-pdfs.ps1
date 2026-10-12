$ErrorActionPreference = "Stop"

$jobName = "alynk-filebunker-dev-cont-job"
$resourceGroup = "alinkdev-res-grp"
$baseUrl = "https://alinkdev.blob.core.windows.net/barcodescanner"

$files = @(
    "202508281152453531.pdf"
    "test2.pdf"
    "test2.pdf"
    "20251031073823966.pdf"
    "test2.pdf"
    "20260602230710244_AL7040.pdf"
    "20260602233755831_AL7053.pdf"
    "20260603183637272_AL7092.pdf"
    "20260804215503231_Scan_2026_08_04_14_53_28_078_processed_20260804_145432.pdf"
    "Kiefer_2026100620010506185d46740307a4bdea6d909c3c46d584d.pdf"
    "Kiefer_20261006200300844b285963e6eee4517bbbfc81f27f20df8.pdf"
    "RO_Booklet_6032548_6032548_6032548.pdf"
    "SCHOSC611074510580155_0ab397c5-411a-46f5-87ce-bc902e3e8daa.pdf"
    "test2.pdf"
    "test3.pdf"
)

$results = @()

foreach ($file in $files) {
    Write-Host "`n===== Processing $file =====" -ForegroundColor Cyan

    $pdfUrl = "$baseUrl/$file"

    az containerapp job update `
        --name $jobName `
        --resource-group $resourceGroup `
        --set-env-vars "INPUT_PDF_URL=$pdfUrl" `
        --output none

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to update PDF URL: $file"
    }

    # Record current executions to identify the new one.
    $before = @(
        az containerapp job execution list `
            --name $jobName `
            --resource-group $resourceGroup `
            --output json | ConvertFrom-Json |
        ForEach-Object { $_.name }
    )

    if ($LASTEXITCODE -ne 0) {
        throw "Could not list executions."
    }

    az containerapp job start `
        --name $jobName `
        --resource-group $resourceGroup `
        --output none

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to start job for $file"
    }

    # Find the newly created execution.
    $executionName = $null

    for ($i = 0; $i -lt 12; $i++) {
        Start-Sleep -Seconds 5

        $current = @(
            az containerapp job execution list `
                --name $jobName `
                --resource-group $resourceGroup `
                --output json | ConvertFrom-Json
        )

        if ($LASTEXITCODE -ne 0) {
            throw "Could not retrieve execution list."
        }

        $newExecution = $current |
            Where-Object { $_.name -notin $before } |
            Select-Object -First 1

        if ($newExecution) {
            $executionName = $newExecution.name
            break
        }
    }

    if (-not $executionName) {
        throw "Could not identify the new execution for $file. Stopping to avoid overlapping runs."
    }

    Write-Host "Execution: $executionName"

    while ($true) {
        Start-Sleep -Seconds 10

        $execution = az containerapp job execution show `
            --name $jobName `
            --resource-group $resourceGroup `
            --job-execution-name $executionName `
            --output json | ConvertFrom-Json

        if ($LASTEXITCODE -ne 0) {
            throw "Could not retrieve status for $executionName"
        }

        $status = $execution.properties.status
        Write-Host "$file : $status"

        if ($status -in @("Succeeded", "Failed", "Stopped", "Degraded")) {
            break
        }
    }

    $results += [PSCustomObject]@{
        File = $file
        Execution = $executionName
        Status = $status
    }

    if ($status -ne "Succeeded") {
        Write-Warning "Execution failed for $file. Remaining files were not started."
        break
    }

    Write-Host "Completed: $file" -ForegroundColor Green
}

Write-Host "`n===== BATCH RESULTS ====="
$results | Format-Table -AutoSize
