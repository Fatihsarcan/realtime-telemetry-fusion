<#
.SYNOPSIS
    Terraform planini uygulamadan ONCE ucret riskine karsi denetler.

.DESCRIPTION
    Plani JSON olarak cozumler ve iki seyi dogrular:
      1) Olusturulacak her kaynak turu, Always Free kapsamindaki beyaz listede mi
      2) Makine boyutlari ucretsiz kotayi asiyor mu

    Ucretli olabilecek tek bir kalem bulursa hata verir ve apply'i engeller.
    Boylece "yanlislikla ucretli kaynak olusturma" ihtimali ortadan kalkar.

.EXAMPLE
    .\preflight.ps1
    Temizse ardindan: terraform apply tfplan
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Always Free kapsaminda olan, ucret uretmeyen kaynak turleri
$allowed = @(
    "oci_core_vcn",
    "oci_core_internet_gateway",
    "oci_core_route_table",
    "oci_core_security_list",
    "oci_core_subnet",
    "oci_core_instance",
    "oci_budget_budget",
    "oci_budget_alert_rule"
)

# Always Free kotalari
$limits = @{ Ocpus = 4; MemoryGb = 24; BootGb = 200 }

Write-Host "==> Plan olusturuluyor" -ForegroundColor Cyan
terraform plan -out=tfplan | Out-Null
if ($LASTEXITCODE -ne 0) { throw "terraform plan basarisiz" }

$plan = terraform show -json tfplan | ConvertFrom-Json
$creating = $plan.resource_changes | Where-Object { $_.change.actions -contains "create" }

$problems = @()

Write-Host "`n==> Olusturulacak kaynaklar" -ForegroundColor Cyan
foreach ($r in $creating) {
    $ok = $allowed -contains $r.type
    $mark = if ($ok) { "[UCRETSIZ]" } else { "[!! RISKLI]" }
    $color = if ($ok) { "Green" } else { "Red" }
    Write-Host ("  {0,-12} {1}" -f $mark, $r.address) -ForegroundColor $color
    if (-not $ok) {
        $problems += "Beyaz listede olmayan kaynak turu: $($r.type) ($($r.address))"
    }
}

Write-Host "`n==> Kota kontrolu" -ForegroundColor Cyan
foreach ($r in $creating | Where-Object { $_.type -eq "oci_core_instance" }) {
    $after = $r.change.after
    $shape = $after.shape
    $ocpus = [double]$after.shape_config[0].ocpus
    $mem = [double]$after.shape_config[0].memory_in_gbs
    $boot = [double]$after.source_details[0].boot_volume_size_in_gbs

    Write-Host ("  shape={0} ocpu={1}/{2} ram={3}/{4}GB disk={5}/{6}GB" -f `
            $shape, $ocpus, $limits.Ocpus, $mem, $limits.MemoryGb, $boot, $limits.BootGb)

    if ($shape -ne "VM.Standard.A1.Flex") {
        $problems += "Shape '$shape' Always Free degil. Yalnizca VM.Standard.A1.Flex ucretsizdir."
    }
    if ($ocpus -gt $limits.Ocpus) { $problems += "OCPU kotasi asiliyor: $ocpus > $($limits.Ocpus)" }
    if ($mem -gt $limits.MemoryGb) { $problems += "Bellek kotasi asiliyor: $mem > $($limits.MemoryGb)" }
    if ($boot -gt $limits.BootGb) { $problems += "Disk kotasi asiliyor: $boot > $($limits.BootGb)" }
}

Write-Host ""
if ($problems.Count -gt 0) {
    Write-Host "DENETIM BASARISIZ - apply calistirmayin:" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Remove-Item tfplan -ErrorAction SilentlyContinue
    exit 1
}

Write-Host "DENETIM TEMIZ - plandaki her kaynak Always Free kapsaminda." -ForegroundColor Green
Write-Host "Uygulamak icin:  terraform apply tfplan" -ForegroundColor Green
Write-Host ""
Write-Host "Hatirlatma: Ucret olusmamasinin asil garantisi hesabin Free Tier olarak" -ForegroundColor Yellow
Write-Host "kalmasidir. Konsoldaki 'Upgrade to Paid Account' butonuna basmayin." -ForegroundColor Yellow
