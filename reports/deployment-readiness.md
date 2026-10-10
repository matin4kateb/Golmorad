# گزارش آمادگی استقرار

تاریخ بازبینی: 2026-10-10

## دامنه و وضعیت مخزن

بازبینی روی checkout محلی `main` انجام شد. پیش از این دور، مخزن تغییرات محلی کاربر در `Golmorad.py`، `README.md`، کد تحلیل/ریسک/SQLite و فایل‌های تازه‌ی paper، backtest، optimizer، GUI و آزمون‌ها داشت. هیچ‌یک از آن تغییرات کنار گذاشته یا بازنویسی کامل نشد. فایل `.env` بازبینی یا چاپ نشد؛ اجرای قبلی `python Golmorad.py --help` طبق کد برنامه از loader عادی startup عبور کرد و loader مقدارهای محلی را به محیط همان فرایند وارد می‌کند، اما خروجی help هیچ‌کدام از آن مقدارها را نشان نداد.

## دور: ایمنی داده‌ی بازار و چرخه‌ی اجرا

- کندل در حال تشکیل دیگر به‌عنوان کندل اجرای کامل در paper یا پایان بک‌تست استفاده نمی‌شود. interval ناشناخته برای تشخیص بسته‌شدن fail closed می‌کند.
- خطاهای تنظیم `APP_LOG_LEVEL`، timeout، نماد، interval بازار و URL feed با پیام روشن رد می‌شوند. timeout به درخواست Yahoo داده می‌شود.
- URL feed در منبع sync به hostname محدود شده و متن خطای network پیش از log/ذخیره‌سازی پالایش می‌شود تا query و اطلاعات احراز هویت احتمالی ثبت نشوند.
- تاریخچه‌ی `sync_runs` به ۱۰۰۰ مورد آخر محدود شده است. خطای بالادستی چرخه‌ی جمع‌آوری دیگر thread زمان‌بند را متوقف نمی‌کند و retry بعدی پس از فاصله‌ی تنظیم‌شده انجام می‌شود.
- SIGINT/SIGTERM توقف دوره‌ای را درخواست می‌کند و shutdown تا تکمیل چرخه‌ی جاری صبر می‌کند. timeout درخواست‌های yfinance و urllib از تنظیم مشترک گرفته می‌شود.
- `--risk-percent` فقط برای برآورد اندازه‌ی موقعیت در تحلیل، همراه با سرمایه‌ی معتبر، پذیرفته می‌شود.
- راهنمای عمومی headless و نمونه‌ی اختیاری systemd همراه backup/restore SQLite در `DEPLOYMENT.md` اضافه شد.

## اعتبارسنجی

- `python -m unittest discover -s tests -v` - PASS, 19 tests; regressions cover forming candles, calendar boundaries, URL redaction, bounded history, and clean shutdown.
- `python -m compileall -q .` - PASS after the final source changes.
- `python Golmorad.py --help` - PASS. Startup loaded `.env` values into that process environment; none were printed or copied into a report.
- `python -m pip check` - PASS (no broken requirements). Local yfinance 1.7.0 introspection confirmed `timeout` support; no provider request was made.
- Headless imports - PASS for CLI, collectors, SQLite, paper, optimizer, and backtest modules. Systemd INI sample parsed with Python `configparser`. `git diff --check` - PASS; Git emitted LF/CRLF conversion notices only.

## وضعیت دروازه‌ها

- **Paper/live:** مسیر سفارش زنده در CLI بسته است و paper از client صرافی جداست. status/paper همچنان به SQLite محلی وابسته‌اند.
- **Headless:** مسیر CLI به GUI import وابسته نیست؛ `tkinter` فقط در گزینه‌ی GUI بارگذاری می‌شود.
- **تنظیمات و اسرار:** `.env` در `.gitignore` است. متغیرهای خالی Bit24 در نمونه‌فایل اختیاری‌اند؛ live برای سرور لازم نیست. `APP_ENV` و `APP_TIMEZONE` در نمونه‌فایل فعلی برای منطق اجرا مصرف نمی‌شوند.
- **وابستگی‌ها:** نسخه‌ی direct dependency در بازه‌ی `requirements.txt` است و lockfile یا pin کامل transitive وجود ندارد. نصب‌های مستقل هنوز دقیقاً تکرارپذیر نیستند.
- **پایش:** CLI `--status` گزارش sync را از DB می‌خواند، اما health/readiness endpoint و سنجش مستقل دسترسی provider وجود ندارد. supervisor باید زنده‌بودن پردازش را پایش کند.
- **ذخیره‌سازی:** sync history محدود است؛ کندل، خبر و داده‌ی paper retention/پاک‌سازی زمان‌بندی‌شده ندارند. سیاست backup باید روی میزبان تعریف و restore دوره‌ای آزموده شود.
- **Bit24:** The official docs page timed out during this review, so current order, precision, minimum, rate-limit, and ambiguous-timeout recovery contracts were not verified. Live must remain disabled.
- **استقرار:** فایل systemd صرفاً نمونه‌ی مستند است. نه نصب dependency روی سرور، نه اجرای systemd، نه backup/restore عملی و نه deploy انجام شد.

## ادامه‌ی لازم بیرون از این محیط

برای بستن موارد مسدود، محیط ساخت باید lock dependency متناسب با سیستم‌عامل/نسخه‌ی Python هدف تولید و در CI نصب آزمایشی شود؛ قرارداد جاری Bit24 باید از مستند رسمی قابل‌دسترسی تأیید و روش تطبیق سفارش پس از timeout مستند و mock-test شود؛ سپس مالک سرویس باید مسیر DB، ظرفیت/retention، پایش و سیاست backup/restore میزبان را نهایی کند. تا آن زمان فقط جمع‌آوری داده، تحلیل و paper آفلاین مناسب‌اند.
