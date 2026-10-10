# راهنمای عمومی استقرار Golmorad

این برنامه یک جمع‌آورنده و تحلیل‌گر دوره‌ای است، نه سرویس HTTP. مسیر سرور headless از `Golmorad.py` اجرا می‌شود و `tkinter` فقط با گزینه‌ی `--gui` بارگذاری می‌شود. اجرای زنده‌ی Bit24 بسته است؛ فرایند سرور فقط داده را جمع‌آوری یا تحلیل می‌کند و paper trading محلی اختیاری است.

## نصب و تنظیم

یک Python سازگار با dependencyهای پروژه روی میزبان نصب کنید. سپس در پوشه‌ی checkout:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` نسخه‌ی مستقیم `yfinance` را در یک بازه محدود می‌کند، اما lockfile وابستگی‌های انتقالی ندارد؛ بنابراین نصب‌ها هنوز کاملاً تکرارپذیر نیستند. پیش از بهره‌برداری، نسخه‌های dependency را در فرایند ساخت محیط خود تثبیت و همان مجموعه را برای استقرار نگه دارید. در این محیط شبکه‌ای برای resolve کردن و ساخت lock معتبر در دسترس نبود.

کاربر سیستم اختصاصی و بدون دسترسی تعاملی بسازید. دایرکتوری داده را به همان کاربر واگذار کنید و مسیر پایگاه داده را بیرون checkout تنظیم کنید:

```sh
install -d -o golmorad -g golmorad -m 0750 /var/lib/golmorad
install -d -o root -g golmorad -m 0750 /etc/golmorad
```

فایل تنظیمات محلی را در `/etc/golmorad/golmorad.env` بسازید و دسترسی آن را به مدیر سیستم محدود کنید (`root:golmorad`, mode `0640`). فقط متغیرهای لازم را قرار دهید؛ secret لازم نیست. متغیرهای پشتیبانی‌شده در اجرای سرور عبارت‌اند از `APP_LOG_LEVEL`, `DATABASE_PATH`, `MARKET_SYMBOL`, `MARKET_INTERVALS`, `DATA_REFRESH_INTERVAL_SECONDS`, `DATA_HTTP_TIMEOUT_SECONDS` و `NEWS_RSS_URLS`. مسیر نسبی پایگاه داده نسبت به ریشه‌ی پروژه تفسیر می‌شود؛ مسیر مطلق برای سرویس مناسب‌تر است. intervalهای بازار باید از بازه‌های Yahoo Finance پشتیبانی‌شده باشند و timeout شبکه باید عدد مثبت و متناهی باشد. مقدار log level نامعتبر یا تنظیمات جمع‌آوری نامعتبر باعث توقف روشن هنگام شروع می‌شوند.

## اجرای دستی و پایش

از ریشه‌ی پروژه اجرا کنید:

```sh
.venv/bin/python Golmorad.py --once
.venv/bin/python Golmorad.py
.venv/bin/python Golmorad.py --status
```

اجرای بدون گزینه، جمع‌آوری فوری انجام می‌دهد و سپس هر ۹۰۰ ثانیه تکرار می‌کند؛ حداقل فاصله ۶۰ ثانیه است. `--once` یک چرخه را اجرا می‌کند. `--status` آخرین نتیجه‌های جمع‌آوری را از SQLite می‌خواند. این فرمان endpoint سلامت HTTP نیست: فعال‌بودن پردازش را supervisor و تازگی منابع را خروجی status بررسی می‌کند. وضعیت پایگاه داده با شکست فرمان قابل تشخیص است. تاریخچه‌ی `sync_runs` به ۱۰۰۰ رکورد آخر محدود شده است.

log برنامه روی stdout/stderr می‌رود؛ آن را به journald یا log collector میزبان بسپارید و rotation/retention را همان‌جا تنظیم کنید. متن خطاهای شبکه و URLهای RSS پیش از ذخیره و log شدن پالایش می‌شوند. هیچ API key لازم نیست.

## نمونه‌ی اختیاری systemd

این نمونه برای Linux با systemd است و باید مسیرها، نام کاربر و سیاست شبکه‌ی میزبان با محیط واقعی تطبیق داده شوند. فایل `/etc/systemd/system/golmorad.service`:

```ini
[Unit]
Description=Golmorad market data collector
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=golmorad
Group=golmorad
WorkingDirectory=/opt/golmorad
EnvironmentFile=/etc/golmorad/golmorad.env
ExecStart=/opt/golmorad/.venv/bin/python /opt/golmorad/Golmorad.py
Restart=on-failure
RestartSec=30s
TimeoutStopSec=180s
KillSignal=SIGTERM
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/golmorad

[Install]
WantedBy=multi-user.target
```

در env file مقدار `DATABASE_PATH=/var/lib/golmorad/golmorad.db` قرار دهید. پس از بررسی unit و دسترسی‌ها، مدیر سیستم می‌تواند آن را فعال کند:

```sh
systemctl daemon-reload
systemctl enable --now golmorad.service
systemctl status golmorad.service
journalctl -u golmorad.service
```

برای توقف تمیز از `systemctl stop golmorad.service` استفاده کنید. SIGTERM حلقه را متوقف می‌کند و تا پایان چرخه‌ی جاری جمع‌آوری صبر می‌کند؛ مقدار `TimeoutStopSec` را متناسب با تعداد feedها و timeout شبکه تنظیم کنید. نصب یا اجرای واقعی systemd در این بازبینی انجام نشده است.

## پشتیبان‌گیری و بازیابی SQLite

SQLite در حالت WAL اجرا می‌شود. برای پشتیبان‌گیری سازگار حین اجرا از SQLite backup API استفاده کنید:

```sh
mkdir -p /var/lib/golmorad/backups
DATABASE_PATH=/var/lib/golmorad/golmorad.db python -c 'import os,sqlite3; src=sqlite3.connect(os.environ["DATABASE_PATH"]); dst=sqlite3.connect("/var/lib/golmorad/backups/golmorad.db"); src.backup(dst); dst.close(); src.close()'
```

پوشه‌ی backup را با دسترسی محدود نگه دارید و نسخه‌های پشتیبان را طبق سیاست میزبان حفظ کنید. برای restore، سرویس را متوقف کنید، نسخه‌ی فعلی DB را جداگانه حفظ کنید، فایل backup را در مسیر `DATABASE_PATH` قرار دهید و مالکیت آن را به کاربر سرویس بدهید؛ سپس سرویس را شروع و `--status` را بررسی کنید. بازیابی عملی و سیاست زمان‌بندی backup در این محیط آزموده نشده‌اند.

## عیب‌یابی و محدودیت‌ها

- شکست شبکه یا provider را در `--status` و journal بررسی کنید؛ collector برای چرخه‌ی بعدی طبق فاصله‌ی تعیین‌شده دوباره تلاش می‌کند.
- `--status` و `--analyze` را برای تشخیص داده‌ی ذخیره‌شده اجرا کنید. API سلامت/آمادگی مستقل یا migration runner جداگانه در پروژه وجود ندارد.
- دیسک باید برای SQLite، فایل‌های WAL و backup فضای کافی داشته باشد. خطای نوشتن پایگاه داده اجرای فرمان را ناموفق می‌کند؛ رشد تاریخچه‌ی sync محدود شده اما داده‌ی بازار، خبر و paper به‌طور نامحدود نگهداری می‌شوند و باید برای اندازه‌ی دیسک پایش شوند.
- برای paper از `--paper --capital ...` و پایگاه SQLite مجزا از داده‌ی عملیاتی استفاده کنید. توقف اضطراری paper در همان DB ذخیره می‌شود.
- هیچ استقرار، اتصال به سرور، سفارش زنده، یا تغییر سرویس بیرونی در این بازبینی انجام نشده است.
