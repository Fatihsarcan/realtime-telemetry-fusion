# Telemetry Fusion

Çoklu kaynaktan gelen telemetri verisini gerçek zamanlı toplayan, normalize eden, tekilleştiren ve hem canlı hem geçmiş sorgu olarak sunan dağıtık veri platformu.

Şu an canlı olarak **OpenSky Network ADS-B** akışını işliyor: uçakların konum, irtifa, hız ve yön verisi 15 saniyede bir çekiliyor, kuyruğa basılıyor, işlenip PostgreSQL'e yazılıyor ve WebSocket üzerinden haritaya anlık olarak iletiliyor.

## Mimari

```
                    ┌──────────────────┐
   OpenSky ADS-B ──►│ collector-opensky│──┐
                    └──────────────────┘  │
                    ┌──────────────────┐  │   ┌──────────┐   ┌───────────┐
   (AIS / diğer) ──►│ collector-*      │──┼──►│ RabbitMQ │──►│ processor │
                    └──────────────────┘  │   └──────────┘   └─────┬─────┘
                                          │    topic exchange      │
                                          │    + dead-letter       │
                                          │                        ▼
                                          │            ┌───────────────────────┐
                                          │            │ normalize → dedup →   │
                                          │            │ batch write           │
                                          │            └───┬───────────────┬───┘
                                          │                ▼               ▼
                                          │        ┌──────────────┐  ┌──────────┐
                                          │        │ PostgreSQL   │  │  Redis   │
                                          │        │ (geçmiş)     │  │ (anlık + │
                                          │        └──────┬───────┘  │  pub/sub)│
                                          │               │          └────┬─────┘
                                          │               ▼               ▼
                                          │           ┌─────────────────────┐
                                          └──────────►│ api (FastAPI)       │
                                                      │ REST + WebSocket    │
                                                      └──────────┬──────────┘
                                                                 ▼
                                                          canlı harita
```

**Neden bu ayrım:** collector yalnızca dış kaynağı bilir, processor yalnızca veri modelini bilir, API yalnızca okuma yapar. Yeni bir veri kaynağı eklemek, yeni bir collector yazıp aynı kuyruğa basmaktan ibaret — diğer servislerin hiçbirine dokunulmaz.

| Bileşen | Görev | Teknoloji |
|---|---|---|
| `collector-opensky` | Dış API'den çekme, ortak modele çevirme, kuyruğa basma | Python, httpx, aio-pika |
| `rabbitmq` | Toplama ve işleme katmanlarını ayırma, geri basınç, dead-letter | RabbitMQ topic exchange |
| `processor` | Tekilleştirme, toplu yazma, canlı yayın | Python, asyncpg, redis |
| `postgres` | Zaman serisi (append-only gözlemler) + son durum tablosu | PostgreSQL 16 |
| `redis` | Anlık durum cache'i + WebSocket fanout kanalı | Redis 7 |
| `api` | REST geçmiş sorgu, WebSocket canlı akış, metrikler | FastAPI, uvicorn |

## Çalıştırma

```bash
cp .env.example .env       # istersen OpenSky hesabını gir (boş da çalışır)
docker compose up -d --build
```

Harita: <http://localhost:8000> · API dokümanı: <http://localhost:8000/docs> · RabbitMQ paneli: <http://localhost:15672>

İlk veri kuyruğa 15 saniye içinde düşer.

## API

| Endpoint | Açıklama |
|---|---|
| `GET /api/tracks?source=&bbox=&limit=` | Tüm nesnelerin son bilinen konumu (Redis'ten, veritabanına dokunmaz) |
| `GET /api/tracks/{source}/{id}/history?minutes=60` | Bir nesnenin geçmiş rotası (PostgreSQL'den) |
| `GET /api/stats` | Pipeline metrikleri: hacim, elenen tekrar sayısı, batch p50/p95 gecikmesi |
| `GET /health` | Redis ve PostgreSQL bağlantılarını gerçekten sınayan sağlık kontrolü |
| `WS /ws/live` | Her yeni gözlemi anlık ileten akış |

```bash
curl "http://localhost:8000/api/tracks?bbox=39,41,28,30&limit=10" | jq
curl "http://localhost:8000/api/stats" | jq
```

## Tasarım kararları

**Neden kuyruk?** Collector saniyede yüzlerce kayıt üretebilir, PostgreSQL yazma hızı buna bağlı değildir. Araya kuyruk koymak, kaynak hızlandığında işleme katmanının çökmesi yerine kuyruğun birikmesini sağlar — geri basınç (backpressure) böyle yönetilir. `prefetch_count`, batch boyutunun iki katıyla sınırlıdır, yani processor belleğinde sınırsız mesaj birikmez.

**Tekilleştirme (dedup) üç katmanlı.** ADS-B'de aynı uçağın aynı zaman damgalı kaydı tekrar tekrar gelir; hiçbir şey yapılmazsa veritabanı gereksiz büyür ve "son 1 dakikadaki gözlem" gibi metrikler yanlış çıkar:
1. Batch içinde aynı nesnenin birden fazla kaydı varsa en yenisi alınır.
2. Redis'teki son zaman damgasıyla karşılaştırılır; eski veya aynı olan kayıt düşürülür. Redis paylaşılan durum olduğu için processor birden fazla kopya çalıştığında da doğru sonuç verir.
3. `observations` tablosundaki `UNIQUE (source, source_id, ts)` kısıtı son savunma hattıdır — uygulama katmanı kaçırırsa veritabanı tutar.

**Neden toplu (batch) yazma?** Tek tek `INSERT` yerine 500'lük gruplar halinde tek transaction'da yazmak, saniyedeki mesaj kapasitesini yaklaşık bir büyüklük mertebesi artırıyor. Trafik seyrekse yarım batch 1 saniyelik zaman aşımıyla boşaltılır, veri beklemede kalmaz.

**Veri kaybı olmaması için:** Batch başarıyla yazılmadan hiçbir mesaj `ack`'lenmez. Processor tam o anda çökerse mesajlar kuyrukta kalır ve yeniden işlenir (at-least-once teslimat). Tekrar işlenen kayıtlar dedup katmanına takıldığı için sonuçta tekilleşir. Çözülemeyen bozuk mesajlar dead-letter kuyruğuna gider, pipeline'ı kilitlemez.

**Geç gelen paketler.** `tracks` tablosundaki upsert, `WHERE EXCLUDED.last_ts > tracks.last_ts` koşuluyla çalışır: ağ gecikmesi yüzünden sıra dışı gelen eski bir paket, daha yeni durumun üstüne yazamaz.

**Kaynak kotası kendi kendine yönetiliyor.** OpenSky günlük kredi kotası uygular ve her sorgunun maliyeti kapsanan alanın büyüklüğüne göre 1-4 kredi arasında değişir. Sabit bir sorgulama aralığı yazmak kırılgan olurdu: bbox değişirse ya kota gün ortasında biter ya da gereksiz yere seyrek veri toplanır. Bunun yerine collector, her cevaptaki `X-Rate-Limit-Remaining` başlığından **gerçek maliyeti ölçüyor** ve "kalan süre ÷ karşılanabilir çağrı sayısı" hesabıyla kendi aralığını ayarlıyor. Kota azaldıkça yavaşlıyor, gün dönüp kota sıfırlanınca hızlanıyor.

**Neden hem Redis hem PostgreSQL?** "Şu an nerede?" sorusu ile "son iki saatte nereden geçti?" sorusunun erişim deseni tamamen farklı. Birincisi anahtar bazlı, çok sık ve tazelik odaklı — Redis. İkincisi aralık taraması, seyrek ve kalıcılık odaklı — PostgreSQL. Canlı harita bu yüzden veritabanına hiç yük bindirmiyor.

**WebSocket fanout.** Her istemci için ayrı Redis aboneliği açmak bağlantıları gereksiz meşgul eder. Tek dinleyici + bellek içi dağıtım, yüzlerce istemcide de sabit maliyetli kalır. Yavaş istemcinin kuyruğu dolarsa mesajı düşürülür — bir istemci tüm yayını yavaşlatamaz.

## Ölçeklendirme

```bash
docker compose up -d --scale processor=3
```

Processor durumsuz (stateless) tasarlandı: paylaşılan durumun tamamı Redis ve PostgreSQL'de. Kuyruk birikmeye başlarsa kopya sayısını artırmak yeterli.

## Sunucuya kurulum (sıfır maliyet)

Tüm yığın ücretsiz katmanlarda çalışır: Oracle Cloud Always Free sunucu, DuckDNS alan adı, Let's Encrypt sertifikası.

1. **Sunucu:** Oracle Cloud → Compute → Instance oluştur → *Ampere A1 (ARM), 2 OCPU / 12 GB, Ubuntu 24.04*. "Always Free eligible" etiketli şekli seç.
2. **Ağ:** Instance'ın subnet'inde Security List → Ingress Rules → `0.0.0.0/0` için TCP 80 ve 443 aç.
3. **Alan adı:** <https://duckdns.org> → GitHub ile giriş → alt alan adı oluştur → sunucunun public IP'sini gir.
4. **Kurulum:** sunucuya SSH ile bağlan ve çalıştır:

```bash
git clone <repo-url> ~/telemetry-fusion && cd ~/telemetry-fusion
bash scripts/server-setup.sh <alt-alan-adiniz>.duckdns.org
```

Script Docker'ı kurar, `.env` dosyasını rastgele parolalarla oluşturur, güvenlik duvarını açar ve üretim kaplamasıyla servisleri başlatır. Caddy sertifikayı birkaç dakika içinde kendisi alır.

Üretimde API doğrudan dışarı açılmaz; tüm trafik Caddy üzerinden HTTPS ile geçer, RabbitMQ paneli dışarıya kapalıdır.

## Güvenlik ve kaynak sınırları

Sistem sabit kaynaklı bir sunucuda çalışacak şekilde tasarlandı: hiçbir bileşen kendiliğinden büyüyemez, dolayısıyla bir hata veya yoğun trafik beklenmedik kaynak tüketimine yol açamaz.

| Sınır | Değer | Neden |
|---|---|---|
| Veri saklama | 7 gün | Tablo süresiz büyüyüp diski doldurmasın; eski kayıtlar parçalı olarak silinir |
| WebSocket istemcisi | 50 | Bir istemci canlı akıştan ayda ~11 GB alır; 50 istemci ~550 GB, ücretsiz çıkış kotasının %6'sı |
| IP başına bağlantı | 5 | Tek istemcinin hatalı yeniden bağlanma döngüsü sunucuyu meşgul edemesin |
| İstemci kuyruğu | 1000 mesaj | Yavaş istemcinin mesajı düşürülür, yayın yavaşlamaz |
| RabbitMQ prefetch | batch × 2 | Processor belleğinde sınırsız mesaj birikmez |
| Konteyner belleği | 256 MB – 1 GB | Üretim kaplamasında her servise sınır konur |
| İstek gövdesi | 64 KB | Yazma ucu olmayan bir servis; büyük gövde baştan reddedilir |

Ayrıca: konteynerler root olmayan kullanıcıyla çalışır, üretimde API ve RabbitMQ doğrudan dışarı açılmaz (yalnızca Caddy 80/443), HSTS ve CSP başlıkları uygulanır, SSH parola ile değil yalnızca anahtarla yapılır ve `allowed_ssh_cidr` ile tek IP'ye kısıtlanabilir.

**Bulut hesabında maliyet koruması** (`infra/`): değişkenlerde Always Free kotası doğrulanır, `preflight.ps1` planı beyaz listeye göre denetleyip ücretli kaynak içeren `apply`'ı engeller, sıfır harcama bütçe alarmı kurulur ve `destroy.ps1` tek komutta her şeyi siler. Bunların hepsinin üstünde duran asıl garanti, hesabın Free Tier olarak kalmasıdır.

## Yol haritası

- [ ] İkinci veri kaynağı (AIS gemi telemetrisi) ve kaynaklar arası korelasyon
- [ ] Elasticsearch ile tam metin ve coğrafi arama
- [ ] Yük testi ve p95 gecikme ölçümleri
- [ ] Prometheus metrik endpoint'i

## Lisans

MIT
