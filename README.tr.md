<h1 align="center">Telemetry Fusion</h1>

<p align="center">
  Gerçek zamanlı, çoklu kaynaklı telemetri toplama ve füzyon platformu.<br>
  Güvenilmez dış kaynaklardan gelen yüksek frekanslı konum verisini toplar, tek bir modele
  normalize eder, tekilleştirir ve hem canlı akış hem sorgulanabilir geçmiş olarak sunar.
</p>

<p align="center"><a href="README.md">🇬🇧 English</a></p>

---

## Ne yapıyor

Platform şu an **OpenSky Network** üzerinden canlı **ADS-B uçak telemetrisi** işliyor: Türkiye hava
sahasındaki yaklaşık 300 uçağın konum, irtifa, hız ve yön bilgisi.

Uçaklar taşınan yük, ürünün kendisi değil. **Ürün, boru hattının kendisi** — kaynak kotayı kestiğinde,
aynı gözlem beş kez geldiğinde, paketler sırasız düştüğünde ve bir servis toplu yazmanın ortasında
çöktüğünde ayakta kalan kısım. Toplayıcıyı değiştirin, aynı hat gemi AIS verisini, İHA telemetrisini,
sensör ağlarını veya araç takibini taşır — alt katmanların hiçbirine dokunmadan.

## Neden var

Akan telemetri göründüğünden zordur. Naif bir uygulama her kaydı doğrudan veritabanına yazar ve
gerçek hayat ilk müdahale ettiğinde devrilir. Bu proje mutlu senaryoya değil, **arıza senaryolarına**
göre kurgulandı:

| Gerçek hayattaki problem | Sistemin çözümü |
|---|---|
| Kaynak kotayı kesiyor veya düşüyor | Toplayıcı loglar, bekler, tekrar dener; hat önbellekten servis vermeye devam eder |
| Aynı gözlem tekrar tekrar geliyor | Üç katmanlı tekilleştirme (batch içi → Redis → veritabanı kısıtı) |
| Paketler sırasız geliyor | Upsert, eski zaman damgasının yeni durumu ezmesini reddeder |
| Bozuk kayıtlar | Girişte doğrulanır; çözülemeyen mesaj dead-letter kuyruğuna gider, hattı kilitlemez |
| Tüketici toplu yazmanın ortasında çöküyor | Batch yazılmadan hiçbir mesaj onaylanmaz — at-least-once teslimat |
| Üretici veritabanını geçiyor | Kuyruk yükü emer, prefetch bellekteki birikimi sınırlar (geri basınç) |
| Kaynak günlük kota uyguluyor | Toplayıcı gerçek maliyeti başlıklardan ölçüp kendi hızını ayarlar |

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
                                          │            │ toplu yazma           │
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

Her servis mümkün olduğunca az şey bilir: toplayıcı dış API'yi bilir ama veritabanını bilmez;
işleyici veri modelini bilir ama verinin nereden geldiğini bilmez; API yalnızca okur. **Yeni bir veri
kaynağı eklemek, bir toplayıcı yazıp aynı exchange'e yayınlamaktan ibarettir** — başka hiçbir şey
değişmez.

| Servis | Sorumluluk | Teknoloji |
|---|---|---|
| `collector-opensky` | Çekme, normalize etme, yayınlama; OAuth2 token yenileme, kota ayarlama | httpx, aio-pika |
| `rabbitmq` | Toplama ile işlemeyi ayırma; geri basınç; dead-letter | RabbitMQ topic exchange |
| `processor` | Tekilleştirme, toplu yazma, canlı yayın, saklama süresi | asyncpg, redis |
| `postgres` | Append-only gözlem zaman serisi + son durum tablosu | PostgreSQL 16 |
| `redis` | Anlık durum önbelleği + WebSocket dağıtım kanalı | Redis 7 |
| `api` | REST geçmiş sorgusu, WebSocket canlı akış, metrikler | FastAPI, uvicorn |

## Hızlı başlangıç

```bash
git clone https://github.com/Fatihsarcan/realtime-telemetry-fusion
cd realtime-telemetry-fusion
cp .env.example .env          # istenirse ücretsiz OpenSky kimlik bilgileri girilir
docker compose up -d --build
```

| | |
|---|---|
| Canlı harita | <http://localhost:8000> |
| API dokümanı | <http://localhost:8000/docs> |
| RabbitMQ paneli | <http://localhost:15672> |

İlk veri bir dakika içinde düşer. OpenSky kimlik bilgisi girilmezse toplayıcı anonim erişime düşer;
çalışır ama günlük kotası çok daha küçüktür.

## API

| Uç nokta | Açıklama |
|---|---|
| `GET /api/tracks?source=&bbox=&limit=` | Takip edilen her nesnenin son konumu — Redis'ten gelir, veritabanına dokunmaz |
| `GET /api/tracks/{source}/{id}/history?minutes=60` | Bir nesnenin geçmiş rotası, PostgreSQL'den |
| `GET /api/stats` | Hat metrikleri: hacim, elenen tekrar, batch gecikmesi p50/p95 |
| `GET /health` | Redis ve PostgreSQL bağlantılarını gerçekten sınayan sağlık kontrolü |
| `WS /ws/live` | Her yeni gözlemi işlendiği anda iter |

## Tasarım kararları

**Kuyruk neden var.** Toplayıcı saniyede yüzlerce kayıt üretebilir; PostgreSQL'in yazma hızı bununla
ilgisizdir. Araya kuyruk koymak, ani yükün tüketiciyi öldürmesi yerine kuyruğun büyümesini sağlar.
`prefetch_count` batch boyutunun iki katıyla sınırlıdır, böylece işleyici bellekte sınırsız mesaj
biriktirmez.

**Tekilleştirme üç katman derinliğinde.** ADS-B aynı uçağı aynı zaman damgasıyla defalarca bildirir.
Elenmezse depolama sınırsız büyür ve "son bir dakikadaki gözlem" gibi metrikler anlamsızlaşır. Bu
yüzden: (1) batch içinde her nesnenin en yeni kaydı kazanır; (2) her aday Redis'teki son zaman
damgasıyla karşılaştırılır — Redis paylaşılan durum olduğu için işleyici birden fazla kopya halinde
ölçeklendiğinde de doğru kalır; (3) `UNIQUE (source, source_id, ts)` kısıtı, uygulama katmanı bir gün
kaçırırsa son savunma hattıdır.

**Toplu yazma.** 500 kaydı tek transaction'da yazmak, 500 ayrı insert'e kıyasla sürdürülebilir hacmi
yaklaşık bir büyüklük mertebesi artırır. Trafik seyrekse 1 saniyelik zamanlayıcı yarım batch'i
boşaltır, veri beklemede kalmaz.

**Çökmede veri kaybı yok.** Hiçbir mesaj, batch'i yazılmadan onaylanmaz. İşleyici uçuş sırasında
ölürse mesajlar kuyrukta kalır ve tekrar teslim edilir — at-least-once. Tekrar gelen kayıtlar
tekilleştirme katmanına takılır, sonuçta etki olarak exactly-once elde edilir. Çözülemeyen mesajlar
sonsuza kadar denenmek yerine dead-letter kuyruğuna gider.

**Sırasız paketler.** Son durum upsert'i `WHERE EXCLUDED.last_ts > tracks.last_ts` koşulu taşır, yani
geciken eski bir paket yeni durumu asla ezemez.

**Neden hem Redis hem PostgreSQL.** "Şu an nerede?" ile "iki saatte nereden geçti?" tamamen farklı
erişim desenleridir — biri anahtar bazlı, sıcak ve tazelik odaklı; diğeri aralık taraması, soğuk ve
kalıcılık odaklı. Ayırmak, canlı haritanın veritabanına sıfır yük bindirmesi anlamına gelir.

**WebSocket dağıtımı.** Tek Redis aboneliği tüm istemcileri bellek içi kuyruklarla besler, böylece
maliyet istemci sayısıyla artmaz. Yavaş istemcinin kuyruğu dolar ve mesajları düşer; yayın herkes
için yavaşlamaz.

**Kaynak kotasına karşı kendi kendini ayarlama.** OpenSky, kapsanan alana göre sorgu başına 1-4 kredi
harcatır ve günlük bir sınır uygular. Sabit bir sorgulama aralığı yazmak kırılgandır. Bunun yerine
toplayıcı gerçek maliyeti `X-Rate-Limit-Remaining` başlığından ölçer ve her turda aralığını
*sıfırlanmaya kalan süre ÷ karşılanabilir çağrı* olarak yeniden hesaplar. Kota azaldıkça yavaşlar,
sıfırlanınca hızlanır.

## Kaynak sınırları

Sistemde hiçbir şey sınırsız büyüyemez; sabit ve küçük bir sunucuda güvenle çalışmasını sağlayan da bu.

| Sınır | Değer | Gerekçe |
|---|---|---|
| Veri saklama | 7 gün | Parçalı silme, tablonun diski doldurmasını engeller |
| WebSocket istemcisi | 50 | Her istemci ayda ~11 GB giden trafik tüketir |
| IP başına bağlantı | 5 | Hatalı yeniden bağlanma döngüsü sunucuyu tek başına meşgul edemez |
| İstemci kuyruk derinliği | 1000 | Yavaş tüketici yayını durdurmak yerine mesaj düşürür |
| Kuyruk prefetch | batch × 2 | İşleyici belleğini sınırlar |
| Konteyner belleği | 256 MB – 1 GB | Üretim kaplamasında zorunlu |
| İstek gövdesi | 64 KB | Yazma ucu yok; büyük gövde kenarda reddedilir |

Konteynerler root olmayan kullanıcıyla çalışır. Üretimde yalnızca Caddy dışarı açıktır (80/443),
otomatik Let's Encrypt sertifikası, HSTS ve CSP ile; API ve RabbitMQ paneli dışarıdan erişilemez.

## Ölçülen performans

Geliştirme dizüstünde, canlı trafiğe karşı ölçüldü — gösterge niteliğinde, kıyaslama değil:

| Metrik | Değer |
|---|---|
| Tur başına uçak | ~300 |
| Saklanan gözlem | 38.000+ |
| Elenen tekrar | 11.600+ |
| Batch yazma gecikmesi (p50 / p95) | 31 ms / 56 ms |
| Kaynaktan ölçülen sorgu maliyeti | çağrı başına 3 kredi |

## Kurulum

`docker-compose.prod.yml` otomatik HTTPS için Caddy ekler ve konteyner bellek sınırlarını uygular:

```bash
DOMAIN=alan.adiniz docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

`infra/` klasörü tüm yığını Oracle Cloud'un daima ücretsiz katmanında Terraform ile kurar — VCN, alt
ağ, güvenlik listesi ve Ampere A1 sunucu; cloud-init ilk açılışta Docker'ı kurar.

Altyapı kodu, yanlışlıkla harcama yapmayı imkânsız kılacak şekilde yazıldı: değişken doğrulamaları
ücretsiz kotanın üstündeki değerleri reddeder, `preflight.ps1` planı ücretsiz kaynak türlerinden
oluşan beyaz listeye göre denetler ve başka bir şey görürse `apply`'ı engeller, sıfır harcama bütçe
alarmı kurulur ve `destroy.ps1` her şeyi tek komutta siler.

## Yol haritası

- [ ] İkinci kaynak (gemi AIS) ve kaynaklar arası korelasyon
- [ ] Elasticsearch ile tam metin ve coğrafi arama
- [ ] Yük testi ve yayımlanmış gecikme dağılımları
- [ ] Prometheus metrik uç noktası

## Lisans

MIT — bkz. [LICENSE](LICENSE).
