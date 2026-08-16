# Kaldığımız yer ve sıradaki adımlar

Son güncelleme: 16 Ağustos 2026

---

## Sistemi ayağa kaldırma

```bash
cd C:\Users\Fatih\Desktop\projeler\telemetry-fusion
docker compose up -d
```

Ardından <http://localhost:8000>. Veriler kalıcı birimde durur, kaldığı yerden devam eder.

Kontrol komutları:

```bash
docker compose ps                          # servislerin durumu
docker compose logs -f processor           # canlı log
curl http://localhost:8000/api/stats       # metrikler
```

---

## Şu an ne çalışıyor

| Bileşen | Durum |
|---|---|
| ADS-B uçak toplayıcı (OpenSky) | ✅ çalışıyor, kimlik doğrulamalı, kotasını kendi ayarlıyor |
| Uydu toplayıcı (Celestrak + SGP4) | ✅ çalışıyor, 157 uydu, anahtar gerektirmiyor |
| Tekilleştirme, toplu yazma, saklama süresi | ✅ |
| Kaynaklar arası korelasyon (füzyon) | ✅ 6-9 ms, tur başına 12-46 eşleşme |
| REST + WebSocket API, canlı harita | ✅ |
| Terraform altyapı kodu + maliyet korumaları | ✅ yazıldı, sunucu henüz kurulamadı |
| GitHub deposu | ✅ public, güncel |

---

## Sıradaki işler (öncelik sırasıyla)

### 1. README'ye ekran görüntüsü — 5 dakika

En yüksek etki/emek oranı olan iş. README'nin tepesinde haritanın görüntüsü olması ilk izlenimi
belirgin şekilde değiştirir.

Yapılacak: sistem çalışırken haritayı açıp `Win+Shift+S` ile görüntü al, `docs/screenshot.png`
olarak kaydet. Ardından her iki README'nin başlık bloğuna `<img>` etiketi eklenir.

### 2. Yükseklik açısı filtresi — 1 saat

Şu an korelasyon yalnızca yer mesafesine bakıyor. Ufka yakın geçen bir uydu pratikte hedefi göremez,
dolayısıyla bu eşleşmeler "kapsama" saymamalı.

`services/processor/fusion.py` içindeki `elevation_angle_deg()` fonksiyonu **yazıldı ama henüz
bağlanmadı**. Yapılacak: korelasyon üretilirken açı hesaplanıp eşiğin (ör. 10°) altındakiler elenecek,
açı `correlations` tablosuna kolon olarak eklenecek ve API cevabında dönecek.

### 3. Yük testi ve p95 ölçümü — 3-4 saat

Mülakatta sayı verebilmek için en değerli iş. "Saniyede şu kadar mesaj işliyor, p95 gecikmesi şu"
diyebilmek, mimari anlatmaktan daha ikna edici.

Yapılacak: sentetik üretici betiği (kuyruğa yüksek hızda sahte gözlem basan), farklı `processor`
kopya sayılarıyla ölçüm, sonuçların README'ye tablo olarak eklenmesi. Ölçülecekler: saniyedeki mesaj,
batch p50/p95, kuyruk birikmesi, `--scale processor=3` ile değişim.

### 4. Elasticsearch entegrasyonu — 3-4 saat

İlanda adı geçen teknolojilerden biri ve şu an eksik. Gözlemler PostgreSQL'e yazılırken
Elasticsearch'e de indekslenir; çağrı işareti/uydu adı üzerinden tam metin ve coğrafi arama sunan bir
uç nokta eklenir. README'de "hangi sorgu neden hangi veritabanına gidiyor" karşılaştırması yazılır.

### 5. Prometheus metrik ucu — 2 saat

`/metrics` uç noktası. İzlenebilirlik tarafını göstermek için.

---

## Açık konular

### Oracle sunucusu kurulamadı

Frankfurt bölgesinde ücretsiz Ampere A1 kapasitesi dolu. 16 Ağustos'ta ~144 deneme yapıldı, hepsi
"Out of host capacity" verdi. Ağ, alt ağ, güvenlik listesi ve bütçe alarmı **kurulu ve bekliyor**;
eksik olan yalnızca sunucu.

Denemeyi sürdürmek için:

```powershell
cd infra
.\retry-capacity.ps1 -Attempts 100 -DelaySeconds 120
```

Kapasite genellikle gece/sabah saatlerinde açılır. Açılırsa çıktıda public IP görünür; sonra DuckDNS
kaydı ve `scripts/server-setup.sh` ile kurulum yapılır.

Alternatif: kalıcı sunucu şart değil. Mülakat anında tek satırla canlı adres alınabilir:

```powershell
& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:8000 --no-autoupdate
```

Hesap, kart, kayıt gerektirmez. Adres her seferinde değişir, o an paylaşılır.

### GitHub katkı grafiği

Commit'ler `mehmetpcp@icloud.com` adresiyle atıldı. Bu adres <https://github.com/settings/emails>
sayfasında kayıtlı ve doğrulanmış değilse commit'ler profilde size ait görünmez (yeşil kareler
işlemez). Kontrol edilmeli; kayıtlı değilse ya eklenmeli ya da commit'ler doğru adresle yeniden
yazılmalı.

---

## Para konusunda hatırlatmalar

- Oracle'da **hiçbir ücretli kaynak yok**; abonelik ekranı €0.00 gösteriyor.
- Konsoldaki **"Upgrade to Paid Account"** butonuna hiçbir zaman basılmamalı. Ücret oluşabilmesinin
  tek yolu budur.
- **14 Eylül 2026**: deneme süresi biter, hesap otomatik olarak Always Free'ye düşer. O tarihte
  gelecek "yükseltin" maillerine tıklanmamalı. Fatura gelmez, kapsam dışı kaynak varsa durdurulur.
- Bir sent hareket olursa `mehmetpcp@icloud.com` adresine bütçe uyarısı gelir.
- Her şeyi silmek gerekirse: `cd infra; .\destroy.ps1`

---

## Mülakata hazırlık notu

Bu proje sorulduğunda anlatılacak çekirdek cümle:

> İki kaynağın biri konumu hazır veriyor, diğeri sadece yörünge elemanı veriyor ve konumu SGP4 ile ben
> hesaplıyorum. İkisini ortak modele indirdikten sonra uzamsal olarak eşleştirip "hangi uydu şu an
> hangi uçağın üzerinden geçiyor" sorusunu cevaplıyorum — kapsama analizi. Korelasyon 47 bin çift için
> 6-9 ms sürüyor.

Gelmesi muhtemel sorular ve cevapların bulunduğu yer: README'nin "Design decisions" / "Tasarım
kararları" bölümü. Kuyruk neden var, veri kaybı neden olmuyor, sırasız paket ne oluyor, neden hem
Redis hem PostgreSQL, kota nasıl yönetiliyor — hepsi orada yazılı.
