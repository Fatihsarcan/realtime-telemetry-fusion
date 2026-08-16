output "public_ip" {
  description = "Sunucunun genel IP adresi. DuckDNS kaydina bunu girin."
  value       = oci_core_instance.app.public_ip
}

output "ssh_command" {
  description = "Sunucuya baglanma komutu"
  value       = "ssh ubuntu@${oci_core_instance.app.public_ip}"
}

output "shape_summary" {
  description = "Olusturulan makinenin ozeti - Always Free kotasiyla karsilastirma"
  value = {
    shape            = oci_core_instance.app.shape
    ocpus            = var.ocpus
    memory_gb        = var.memory_in_gbs
    boot_volume_gb   = var.boot_volume_size_in_gbs
    always_free_kota = "4 OCPU / 24 GB / 200 GB disk"
  }
}

output "maliyet_notu" {
  description = "Maliyet durumu"
  value       = "Bu yapilandirmadaki tum kaynaklar Always Free kapsamindadir. Hesap 'Free Tier' olarak kaldigi surece (Upgrade to Paid Account'a basilmadigi surece) ucret olusamaz."
}
