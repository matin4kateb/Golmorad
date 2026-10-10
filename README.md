# پایگاه دانش بیت‌کوین Golmorad

پایگاه داده‌ی SQLite در `Database/golmorad.db` کندل‌های بازار، خبرهای بیت‌کوین، شاخص ترس‌وطمع و تاریخچه‌ی همگام‌سازی را نگه می‌دارد. مسیر پایگاه داده و چرخه‌ی جمع‌آوری از متغیرهای محیطی قابل تنظیم‌اند.

## راه‌اندازی

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python Golmorad.py --once
```

برای به‌روزرسانی پیوسته، برنامه را بدون `--once` اجرا کنید. چرخه‌ی پیش‌فرض هر ۱۵ دقیقه اجرا می‌شود؛ با `DATA_REFRESH_INTERVAL_SECONDS` یا گزینه‌ی `--interval` قابل تغییر است. حداقل فاصله ۶۰ ثانیه است. برای اجرای برنامه به اینترنت نیاز است.

## تنظیم منابع

- `MARKET_SYMBOL` نماد Yahoo Finance است و پیش‌فرض `BTC-USD` است.
- `MARKET_INTERVALS` بازه‌های کندل جداشده با ویرگول است؛ پیش‌فرض `1d,1h`.
- `NEWS_RSS_URLS` فهرست URLهای RSS/Atom جداشده با ویرگول است. خبرهایی که عنوان یا خلاصه‌شان به Bitcoin/BTC اشاره دارد ذخیره می‌شوند.
- شاخص ترس‌وطمع از API عمومی Alternative.me دریافت می‌شود. تاریخچه‌ی شاخص در بار اول دریافت و سپس فقط آخرین مقادیر همگام می‌شوند.
- هر منبع به‌طور مستقل خطاهایش را در `sync_runs` ثبت می‌کند؛ شکست یک منبع مانع ذخیره‌ی خروجی منابع دیگر نمی‌شود.

## دسترسی از تحلیل‌گرها

`Database.SQLiteUtils.SQLiteUtils` متدهای `get_candles`، `get_news` و `get_sentiment` را فراهم می‌کند. `TradeUtils.TechnicalAnalyzer.TechnicalAnalyzer.analyze()` شاخص‌های SMA، EMA، RSI، ATR، نوسان بازده و بازده آخر را از کندل‌های ذخیره‌شده می‌سازد. `TradeUtils.FundamentalAnalyzer.FundamentalAnalyzer.analyze()` خبرها و مقدار/تاریخچه‌ی شاخص ترس‌وطمع را برای لایه‌ی تصمیم‌گیری برمی‌گرداند.

داده‌ی خبر و احساسات، ورودی تحلیلی است و به‌تنهایی سیگنال خریدوفروش محسوب نمی‌شود. Yahoo Finance و RSS ممکن است محدودیت دسترسی یا تأخیر داشته باشند؛ وضعیت هر بار دریافت در جدول `sync_runs` قابل بررسی است.
