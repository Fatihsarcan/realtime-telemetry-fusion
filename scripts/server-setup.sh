#!/usr/bin/env bash
# Oracle Cloud Always Free (Ubuntu 22.04/24.04, ARM veya x86) uzerinde
# Telemetry Fusion'i sifir maliyetle ayaga kaldirir.
#
# Kullanim (sunucuda):
#   curl -fsSL <repo-raw-url>/scripts/server-setup.sh | bash -s -- <domain> <repo-url>
# veya repoyu klonlayip:
#   bash scripts/server-setup.sh telemetry-fusion.duckdns.org

set -euo pipefail

DOMAIN="${1:-}"
REPO_URL="${2:-}"
APP_DIR="${HOME}/telemetry-fusion"

if [[ -z "$DOMAIN" ]]; then
  echo "Kullanim: bash server-setup.sh <domain> [repo-url]" >&2
  exit 1
fi

echo "==> Paketler guncelleniyor"
sudo apt-get update -qq
sudo apt-get install -y -qq ca-certificates curl git

echo "==> Docker kuruluyor (zaten varsa atlanir)"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
fi

echo "==> Depo hazirlaniyor"
if [[ -n "$REPO_URL" && ! -d "$APP_DIR" ]]; then
  git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  # Uretimde varsayilan sifrelerle calisilmaz
  sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 16)/" .env
  sed -i "s/^RABBITMQ_PASSWORD=.*/RABBITMQ_PASSWORD=$(openssl rand -hex 16)/" .env
  echo "DOMAIN=${DOMAIN}" >> .env
  echo "    .env olusturuldu, rastgele sifreler atandi"
fi

echo "==> Guvenlik duvari: 80 ve 443 aciliyor"
# Oracle imajlarinda iptables varsayilan olarak her seyi kapatir.
# NOT: Ayrica Oracle konsolundan Security List / NSG kurallarini da eklemelisiniz.
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT || true
sudo netfilter-persistent save 2>/dev/null || sudo iptables-save | sudo tee /etc/iptables/rules.v4 >/dev/null 2>&1 || true

echo "==> Servisler baslatiliyor"
sudo docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

echo
echo "Tamamlandi. Birkac dakika icinde sertifika alinir:"
echo "   https://${DOMAIN}"
echo
echo "Durum:   sudo docker compose ps"
echo "Loglar:  sudo docker compose logs -f processor"
