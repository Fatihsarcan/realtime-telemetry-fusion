<#
.SYNOPSIS
    Acil durdurma: Terraform'un olusturdugu her seyi siler.

.DESCRIPTION
    Suphelendiginiz herhangi bir anda calistirin. Sunucu, disk, ag ve butce
    dahil bu proje icin olusturulan tum kaynaklar silinir; geriye ucret
    uretebilecek hicbir sey kalmaz.

    Veritabanindaki telemetri gecmisi de silinir - onemliyse once yedekleyin.
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Bu islem sunucuyu ve uzerindeki TUM veriyi kalici olarak siler." -ForegroundColor Yellow
$answer = Read-Host "Devam etmek icin 'sil' yazin"

if ($answer -ne "sil") {
    Write-Host "Iptal edildi, hicbir sey silinmedi." -ForegroundColor Cyan
    exit 0
}

terraform destroy -auto-approve
Write-Host "`nTum kaynaklar silindi. Hesapta ucret uretebilecek kalem kalmadi." -ForegroundColor Green
