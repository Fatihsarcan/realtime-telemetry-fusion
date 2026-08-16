<#
.SYNOPSIS
    "Out of host capacity" hatasina karsi otomatik yeniden deneme.

.DESCRIPTION
    Oracle'in ucretsiz Ampere kapasitesi cok talep gorur ve sik sik dolu olur.
    Kapasite genellikle baskalari makinelerini sildikce dakikalar icinde acilir,
    bu yuzden dogru cozum beklemek ve tekrar denemektir.

    Betik her turda uc availability domain'i sirayla dener. Ag, alt ag ve butce
    zaten olusturuldugu icin her deneme yalnizca eksik olan sunucuyu yaratmaya
    calisir; basarisiz denemeler hicbir kaynak birakmaz ve ucret uretmez.

.PARAMETER Attempts
    Toplam deneme turu sayisi (varsayilan 120).

.PARAMETER DelaySeconds
    Turlar arasi bekleme (varsayilan 90 saniye).

.EXAMPLE
    .\retry-capacity.ps1
    .\retry-capacity.ps1 -Ocpus 1 -MemoryGb 6
#>

param(
    [int]$Attempts = 120,
    [int]$DelaySeconds = 90,
    [int]$Ocpus = 2,
    [int]$MemoryGb = 12
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

Write-Host "Kapasite bekleniyor: $Ocpus OCPU / $MemoryGb GB, $Attempts tur x $DelaySeconds sn"

for ($i = 1; $i -le $Attempts; $i++) {
    foreach ($ad in 0, 1, 2) {
        $output = terraform apply -auto-approve `
            -var "availability_domain_index=$ad" `
            -var "ocpus=$Ocpus" `
            -var "memory_in_gbs=$MemoryGb" 2>&1 | Out-String

        if ($output -match "Apply complete") {
            $ip = terraform output -raw public_ip
            Write-Host "`nBASARILI (tur $i, AD $ad). Sunucu IP: $ip" -ForegroundColor Green
            exit 0
        }

        if ($output -notmatch "Out of host capacity") {
            Write-Host "`nBeklenmeyen hata (AD $ad):" -ForegroundColor Red
            Write-Host $output
            exit 1
        }
    }

    $stamp = Get-Date -Format "HH:mm:ss"
    Write-Host "[$stamp] tur $i/$Attempts - uc AD de dolu, bekleniyor"
    if ($i -lt $Attempts) { Start-Sleep -Seconds $DelaySeconds }
}

Write-Host "`n$Attempts tur denendi, kapasite acilmadi." -ForegroundColor Yellow
exit 2
