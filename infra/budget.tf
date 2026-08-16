# Butce alarmi.
#
# DURUSTCE: OCI butceleri yalnizca UYARI gonderir, kaynagi otomatik silmez.
# Ucret olusmamasinin gercek garantisi hesabin "Free Tier" olarak kalmasidir
# (Konsoldaki "Upgrade to Paid Account" butonuna hicbir zaman basilmamasi).
# Bu kaynak, beklenmedik bir kalem olusursa haberdar olmak icindir.
#
# Butce kaynagi ucretsizdir, kendisi ucret uretmez.

variable "budget_alert_email" {
  description = "Butce uyarisinin gonderilecegi e-posta. Bos birakilirsa butce olusturulmaz."
  type        = string
  default     = ""
}

resource "oci_budget_budget" "zero_spend" {
  count = var.budget_alert_email != "" ? 1 : 0

  compartment_id = var.tenancy_ocid # butce her zaman kok compartment'ta olusur
  target_type    = "COMPARTMENT"
  targets        = [local.compartment_id]
  amount         = 1 # 1 USD
  reset_period   = "MONTHLY"
  display_name   = "${var.instance_name}-sifir-harcama"
  description    = "Herhangi bir ucret olusursa uyar. Beklenen harcama: 0."
}

resource "oci_budget_alert_rule" "any_spend" {
  count = var.budget_alert_email != "" ? 1 : 0

  budget_id      = oci_budget_budget.zero_spend[0].id
  display_name   = "herhangi-bir-harcama"
  type           = "ACTUAL"
  threshold      = 1 # 1 USD butcenin %1'i, yani ~1 sent
  threshold_type = "PERCENTAGE"
  recipients     = var.budget_alert_email
  message        = "Telemetry Fusion hesabinda beklenmeyen bir ucret olustu. Kaynaklari kontrol edin."
}
