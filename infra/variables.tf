# --- OCI kimlik bilgileri -----------------------------------------------------
# Bu degerlerin tamami Konsol > Profil > User Settings > API Keys ekraninda
# anahtar olusturunca gosterilen yapilandirma parcasindan kopyalanir.

variable "tenancy_ocid" {
  description = "Kiraci (tenancy) OCID"
  type        = string
}

variable "user_ocid" {
  description = "Kullanici OCID"
  type        = string
}

variable "fingerprint" {
  description = "API anahtarinin parmak izi"
  type        = string
}

variable "private_key_path" {
  description = "Indirilen OCI API ozel anahtarinin dosya yolu"
  type        = string
}

variable "region" {
  description = "Home region, or. eu-frankfurt-1"
  type        = string
}

variable "compartment_ocid" {
  description = "Kaynaklarin olusturulacagi compartment. Bos birakilirsa kok compartment (tenancy) kullanilir."
  type        = string
  default     = ""
}

# --- Makine ------------------------------------------------------------------

variable "instance_name" {
  description = "Olusturulacak sunucunun adi"
  type        = string
  default     = "telemetry-fusion"
}

# DIKKAT: Always Free kotasi toplam 4 OCPU ve 24 GB. Asagidaki degerler
# kotanin yarisi kadar; bu haliyle ucret uretmez.
variable "ocpus" {
  description = "Ampere A1 cekirdek sayisi (Always Free toplam sinir: 4)"
  type        = number
  default     = 2

  validation {
    condition     = var.ocpus >= 1 && var.ocpus <= 4
    error_message = "Always Free sinirini asmamak icin ocpus 1-4 arasinda olmali."
  }
}

variable "memory_in_gbs" {
  description = "RAM (Always Free toplam sinir: 24 GB)"
  type        = number
  default     = 12

  validation {
    condition     = var.memory_in_gbs >= 1 && var.memory_in_gbs <= 24
    error_message = "Always Free sinirini asmamak icin bellek 1-24 GB arasinda olmali."
  }
}

variable "boot_volume_size_in_gbs" {
  description = "Onyukleme diski (Always Free toplam sinir: 200 GB)"
  type        = number
  default     = 50

  validation {
    condition     = var.boot_volume_size_in_gbs >= 50 && var.boot_volume_size_in_gbs <= 200
    error_message = "Onyukleme diski 50-200 GB arasinda olmali."
  }
}

# Ucretsiz Ampere kapasitesi bolgelere gore degisir; "Out of host capacity"
# hatasi alirsaniz bu degeri 1 veya 2 yapip tekrar deneyin.
variable "availability_domain_index" {
  description = "Kullanilacak availability domain sirasi (0, 1 veya 2)"
  type        = number
  default     = 0
}

variable "ssh_public_key_path" {
  description = "Sunucuya yetkilendirilecek SSH acik anahtari (.pub)"
  type        = string
}

# --- Uygulama ----------------------------------------------------------------

variable "allowed_ssh_cidr" {
  description = "SSH erisimine izin verilen CIDR. Kendi IP'nizle sinirlamak daha guvenli, or. 88.1.2.3/32"
  type        = string
  default     = "0.0.0.0/0"
}
