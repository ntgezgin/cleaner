# disk-cleaner

Diskinin neden dolduğunu bulan, **salt-okunur** bir analiz aracı. Windows, macOS ve Linux'ta
çalışır. Sonucu internetsiz çalışan **tek dosyalık interaktif bir HTML** olarak verir. Temizlik
yapmaz: senin seçtiğin maddelerden, varsayılan olarak **dry-run** çalışan bir temizlik script'i
üretir. Script'i sen inceleyip kendin çalıştırırsın.

- Sadece Python 3.8+ gerekir. Ek paket, internet bağlantısı ya da yönetici/sudo yetkisi gerekmez.
- Tarama sırasında hiçbir dosya silinmez, taşınmaz, yeniden adlandırılmaz ya da değiştirilmez.
- Rapor dış kaynak yüklemez (CDN ve API çağrısı yok). Veriler HTML'in içine gömülüdür.

## Hızlı başlangıç

Her bilgisayarda aynı:

```bash
git clone https://github.com/ntgezgin/cleaner.git
```

```bash
cd cleaner
```

```bash
python -m disk_cleaner
```

Windows'ta `python` yerine `py` gerekebilir. Komut, bulunduğun klasöre
`disk-report-<bilgisayar>-<tarih>.html` yazar. Dosyayı tarayıcıda aç.

Tek dosya olarak taşımak istersen (USB bellek, başka makine):

```bash
python tools/build_pyz.py
```

```bash
python dist/disk-cleaner.pyz
```

`pip install .` ile kurarsan komutun adı `disk-cleaner` olur.

### Sık kullanılan seçenekler

| Seçenek | Ne yapar |
|---|---|
| `--home-only` | Sadece ev klasörünü tarar (en hızlısı) |
| `--skip-system` | `C:\Windows` / `/System` / `/usr` gibi sistem klasörlerini atlar |
| `--never PATH` | Dokunulmaz klasör ekler (tekrarlanabilir) |
| `--exclude PATH` | Klasörü hiç taramaz |
| `--root PATH` | Varsayılan kökler yerine sadece bunları tarar |
| `--all-drives` | Çıkarılabilir sürücüleri de tarar |
| `--no-git` / `--no-docker` | `git status` / `docker system df` kontrollerini atlar |
| `--json r.json` | Ham veriyi ayrıca kaydeder (`disk-cleaner render r.json` ile yeniden HTML üretilir) |

## Makineye özel ayarlar

Her bilgisayarın dokunulmaz klasörleri farklıdır. Bunlar kodda değil, ayar dosyasında durur.
`config.example.json` dosyasını şu konumlardan birine kopyala:

| İşletim sistemi | Konum |
|---|---|
| Hepsi | Çalıştırdığın klasörde `disk-cleaner.json` |
| Windows | `%APPDATA%\disk-cleaner\config.json` |
| macOS | `~/Library/Application Support/disk-cleaner/config.json` |
| Linux | `~/.config/disk-cleaner/config.json` |

İstersen `--config yol.json` ile de verebilirsin.

```json
{
  "never_paths": ["D:\\Projeler", "D:\\Datasets", "~/Recordings"],
  "exclude_paths": ["E:\\Backups"],
  "stale_days": 180
}
```

`never_paths` altındaki hiçbir şey öneri olamaz. Rapordaki script de bu yolları çalışma anında
tekrar kontrol eder ve reddeder.

## Kategoriler

| Kategori | Anlamı | Rapordaki davranış |
|---|---|---|
| **Safe** | Yeniden oluşturulabilir cache/temp: npm, pip, uv, conda pkgs, Gradle, NuGet, Cargo, Go, tarayıcı *cache* klasörleri, GPU shader cache, TEMP, crash dump, Xcode DerivedData… | Varsayılan seçili |
| **Review** | Karar senin: `node_modules`, `.venv`, `target`/`build`/`dist`, conda ortamları, Docker image'ları, HuggingFace/torch/LM Studio modelleri, Android/Xcode imajları, eski toolchain'ler, Downloads'taki büyük installer'lar, telefon yedekleri | Varsayılan seçili değil |
| **Never** | Oyunlar ve kayıtları, Pictures/Videos/Music, Documents/Desktop'taki dosyalar, OneDrive/iCloud/Google Drive/Dropbox, kaynak kod ve `.git`, veri setleri, model ağırlıkları, sanal makineler, Docker volume'ları, veritabanları, tarayıcı profilleri, anahtarlar, e-posta arşivleri, sistem klasörleri | Gösterilir ama seçilemez |

Önemli kurallar:

- **Kanıt yoksa öneri yok.** `node_modules` ancak yanında `package.json` varsa, `target/` ancak
  yanında `Cargo.toml` varsa, bir klasör ancak içinde `pyvenv.cfg` varsa venv sayılır. Kanıtlar
  raporda yazar.
- **Sert kilit:** oyun, bulut, medya, anahtar, VM ve senin `never_paths` klasörlerinin altında hiçbir
  kural çalışmaz.
- **Yumuşak kilit:** Documents, Desktop ve kaynak kod projeleri NEVER'dır. İçlerinde sadece kanıtı
  olan build/bağımlılık klasörleri REVIEW olarak önerilir.
- Bir cache'in içinde veri, medya ya da model dosyaları (csv, wav, pt, safetensors, jpg…) varsa
  Safe'ten Review'a düşürülür. İçinde git deposu olan bir build klasörü seçilemez.
- Bulut "files on demand" yer tutucuları diskte yer kaplamadığı için ~0 sayılır. Boyutlar *size on
  disk*'tir. Hardlink'ler bir kez sayılır. Symlink ve junction'lar takip edilmez.
- `hiberfil.sys` ve `pagefile.sys` sadece öneri olarak gösterilir (`powercfg /h off` gibi). Büyük
  programlar ve oyunlar için "kendi uninstaller'ı / launcher'ı ile kaldır" denir.

## Temizlik script'i

Raporda maddeleri seç ve **Cleanup script üret**'e bas. Windows'ta PowerShell, macOS ve Linux'ta
bash script'i üretilir. Kopyalayabilir ya da indirebilirsin.

Önce önizleme (dry-run, hiçbir şey değişmez):

```bash
bash ~/Downloads/disk-cleanup-XXXX.sh
```

```powershell
powershell -ExecutionPolicy Bypass -File "$HOME\Downloads\disk-cleanup-XXXX.ps1"
```

Gerçekten uygulamak için bash'te `--apply`, PowerShell'de `-Apply` ekle. Script devam etmeden önce
`EVET` yazmanı ister.

Script'in güvenlik önlemleri:

- Silmez, **Çöp Kutusu / Geri Dönüşüm Kutusu'na taşır**. Linux'ta `gio trash` veya `trash-put`,
  macOS'ta `trash` veya Finder kullanılır. Araç yoksa madde atlanır. `rm -rf` ya da
  `Remove-Item -Recurse -Force` kullanılmaz.
- Varsa aracın kendi resmi komutunu kullanır: `npm cache clean --force`, `pip cache purge`,
  `uv cache clean`, `conda clean --all`, `cargo clean`, `go clean -cache`, `dotnet nuget locals`,
  `docker system prune` (asla `--volumes` olmadan), Windows Disk Temizleme, `DISM /StartComponentCleanup`.
- Her yolu çalışma anında tekrar kontrol eder: hâlâ var mı, symlink/junction mı, NEVER listesinde
  mi. İlgili uygulama açıksa (tarayıcı, VS Code…) maddeyi atlar. Yönetici gerektiren maddeleri,
  script yönetici olarak çalışmıyorsa atlar.
- Raporun üretildiği kullanıcı/ev klasörü dışında çalışmayı reddeder.

Yer, Çöp Kutusu boşaltıldığında açılır.

## Bilinen sınırlar

- "Not scanned" kartı bir tahmindir: disklerdeki dolu alan ile taranan toplam arasındaki farktır.
  Sistem dosyaları, diğer kullanıcılar, izin verilmeyen klasörler, dosya sistemi metadata'sı ve
  snapshot'lar bu farkın içindedir.
- Son erişim zamanı (atime) birçok sistemde güncellenmez. "Uzun süredir dokunulmamış" kararı son
  *değiştirme* zamanına göre verilir.
- pnpm, uv ve conda hardlink kullanır. Bu yüzden "kısmi kazanç" diye işaretlenir; toplamlara
  sadece "en fazla" olarak eklenir.
- macOS'ta APFS klonları ve yerel Time Machine snapshot'ları ölçülemez. `~/Library` altındaki bazı
  klasörler için Terminal'e "Full Disk Access" izni gerekebilir; izin yoksa o klasörler "Not
  scanned" olarak listelenir.
- Windows'ta 256 KB altındaki dosyalarda hardlink tekilleştirmesi yapılmaz (hız için). Bu durum
  sadece WinSxS gibi zaten NEVER olan sistem klasörlerini biraz şişkin gösterir.

## Geliştirme

```bash
python -m unittest discover -s tests -v
```

Testler sahte bir ev klasörü kurar. Taramanın hiçbir dosyayı değiştirmediğini, NEVER alanlarından
öneri çıkmadığını, script'in dry-run'da hiçbir şeyi değiştirmediğini ve `--apply` ile sadece seçili
maddeleri çöpe taşıdığını doğrular. Script üretici testleri için Node.js gerekir. CI bu testleri
Windows, macOS ve Linux'ta çalıştırır (`.github/workflows/ci.yml`).

Yapı:

```
disk_cleaner/
  platform_info.py  işletim sistemi, diskler, bilinen klasörler, bulut klasörleri
  scanner.py        os.scandir tabanlı tarayıcı (symlink takip etmez, hardlink tekilleştirir)
  rules.py          Safe / Review / Never kuralları
  classify.py       kuralları ağaca uygular, önerileri üretir
  probes.py         salt-okunur git status / docker system df
  report.py         veriyi toplar, tek dosyalık HTML'i üretir
  ui/               arayüz (template.html, style.css, app.js, scriptgen.js)
```
