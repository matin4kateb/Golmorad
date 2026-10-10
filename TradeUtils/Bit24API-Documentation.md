# راهنمای محلی API صرافی Bit24

این راهنما endpointهای مستندشدهٔ Bit24 و نحوهٔ استفاده از کلاینت محلی [Bit24API.py](Bit24API.py) را پوشش می‌دهد. مبنا، مستندات رسمی <https://docs.bit24.cash/> و کلاس‌های فعلی پروژه است. نشانی پایهٔ REST برابر **https://rest.bit24.cash** است.

> **توجه:** API ممکن است تغییر کند. سفارش‌گذاری، لغو سفارش و برداشت روی حساب واقعی اثر دارند؛ مقادیر، شبکه و مقصد را پیش از ارسال بررسی کنید.

## فهرست مطالب

- [شروع سریع](#شروع-سریع)
- [احراز هویت و امضا](#احراز-هویت-و-امضا)
- [قواعد درخواست و پاسخ](#قواعد-درخواست-و-پاسخ)
- [بازار و داده‌های معاملاتی](#بازار-و-دادههای-معاملاتی)
- [سفارش‌ها](#سفارشها)
- [کیف پول و دارایی](#کیف-پول-و-دارایی)
- [واریز و برداشت](#واریز-و-برداشت)
- [خطاها](#خطاها)
- [فهرست متدهای کلاینت](#فهرست-متدهای-کلاینت)
- [ملاحظات ربات خودکار](#ملاحظات-ربات-خودکار)

## شروع سریع

کلاینت با کتابخانه‌های استاندارد پایتون نوشته شده و به requests نیاز ندارد.

~~~python
from TradeUtils.Bit24API import Bit24API, Bit24HTTPError

client = Bit24API(
    api_key="توکن API",
    api_secret="کلید اختصاصی امضا",
    timeout=15,
)

try:
    response = client.get_markets(page=1)
    markets = response["data"]["results"]
except Bit24HTTPError as exc:
    print(exc.status_code, str(exc), exc.response)
~~~

نام کلاس اصلی **Bit24API** است و **Bit24Client** نیز alias همان کلاس است. بسته به تنظیمات پروژه، ممکن است لازم باشد مسیر import را با ساختار package خود هماهنگ کنید.

## احراز هویت و امضا

### توکن API

توکن را از بخش مدیریت API حساب Bit24 دریافت کنید. کلاینت آن را در هدر زیر می‌فرستد:

~~~http
X-BIT24-APIKEY: <API_TOKEN>
~~~

توکن و کلید اختصاصی را در کد، مخزن Git، گزارش‌ها یا پیام‌ها قرار ندهید. مستندات توصیه می‌کنند IPهای مجاز کلید را محدود کنید؛ در صورت افشا، کلید را از پنل باطل و جایگزین کنید.

### امضای HMAC-SHA256

ثبت سفارش، لغو یک سفارش و ثبت برداشت به امضای کلید اختصاصی نیاز دارند. طبق نمونهٔ مستندات، پارامترها را بر اساس نام کلید الفبایی مرتب کنید و به صورت key=value با & به هم بچسبانید. رشتهٔ حاصل پیام HMAC و کلید اختصاصی کلید HMAC با الگوریتم SHA-256 است؛ خروجی hex به عنوان signature ارسال می‌شود. خود فیلد signature جزو رشتهٔ امضا نیست.

نمونهٔ رشتهٔ canonical:

~~~text
amount=1000&condition_price=3000&price=2500
~~~

کلاینت این کار را خودکار در متدهای امضاشده انجام می‌دهد. برای ساخت دستی:

~~~python
signature = Bit24API.create_signature(
    {"amount": "1000", "condition_price": "3000", "price": "2500"},
    "کلید اختصاصی",
)
~~~

نمایش متنی اعداد را ثابت نگه دارید؛ مثلاً "1.2500" را به 1.25 تبدیل نکنید. مقادیر None از بدنه و امضا حذف می‌شوند. اگر پارامتر اختیاری باید با مقدار صفر فرستاده شود، مقدار 0 را صریحاً ارسال کنید.

## قواعد درخواست و پاسخ

- درخواست‌های GET پارامترها را در query string می‌فرستند.
- بدنهٔ درخواست‌های POST به شکل application/x-www-form-urlencoded است.
- endpointهای این راهنما به توکن نیاز دارند؛ متدهای GET برای داده‌های بازار و اطلاعات واریز/برداشت امضای اختصاصی نمی‌خواهند.
- محدودیت مستندشده برای endpointهای این راهنما ۲۵۰ درخواست در دقیقه است. کلاینت نیز limiter محلی دارد؛ limiter هر نمونه مستقل است و محدودیت حساب/IP را میان چند پردازه هماهنگ نمی‌کند.
- در موفقیت، کلاینت دیکشنری JSON کامل شامل envelope را برمی‌گرداند؛ داده معمولاً در response["data"] است.
- در HTTP error، JSON نامعتبر، یا envelope با success: false، کلاینت Bit24HTTPError می‌اندازد. جزئیات پاسخ در exc.response و کد وضعیت در exc.status_code قرار می‌گیرد.

## بازار و داده‌های معاملاتی

تمام مسیرهای بازار در بخش Pro نیازمند توکن هستند و طبق مستندات به امضای کلید اختصاصی نیاز ندارند.

### فهرست بازارها

| مشخصه | مقدار |
|---|---|
| مسیر | GET /pro/capi/v1/markets |
| کلاینت | get_markets(page=1, **params) |
| پارامتر | page شمارهٔ صفحه؛ پیش‌فرض 1 |

~~~python
response = client.get_markets(page=1)
rows = response["data"]["results"]
~~~

پاسخ می‌تواند total_data، current_page، last_page، per_page و results داشته باشد. هر ردیف اطلاعات جفت‌ارز، شناسهٔ بازار، نماد ارز پایه و مقابل، دقت اعشاری و وضعیت بازار را دارد. فیلدهای قیمت، حجم ۲۴ ساعته، تغییر قیمت و حداقل سفارش نیز ممکن است در پاسخ باشند.

### دفتر سفارش

| مشخصه | مقدار |
|---|---|
| مسیر | GET /pro/capi/v1/markets/order-books |
| کلاینت | get_order_book(base_coin, quote_coin) |
| پارامترهای لازم | base_coin و quote_coin؛ نمونه: BTC و IRT |

~~~python
book = client.get_order_book("BTC", "IRT")["data"]
bids = book.get("buy_orders", [])
asks = book.get("sell_orders", [])
~~~

پاسخ شامل buy_orders و sell_orders است. نمونه‌های مستندات در ردیف‌ها از price، amount، total و نیز each_price، available_amount نام می‌برند؛ ساختار پاسخ واقعی را بررسی کنید.

### OHLC-V

| مشخصه | مقدار |
|---|---|
| مسیر | GET /pro/capi/v1/markets/ohclv |
| کلاینت | get_ohlcv(**params) |
| پارامترهای نمونه | from_ts، to_ts، base_coin_symbol، quote_coin_symbol، resolution |

املای ohclv در مسیر رسمی همین‌طور است و کلاینت نیز همین مسیر را استفاده می‌کند.

~~~python
candles = client.get_ohlcv(
    from_ts=1727770855,
    to_ts=1727778055,
    base_coin_symbol="USDT",
    quote_coin_symbol="IRT",
    resolution="1m",
)["data"]["results"]
~~~

هر کندل در نمونهٔ مستندات فیلدهای t زمان، o بازشدن، c بسته‌شدن، h بیشینه، l کمینه و v حجم را دارد. واحد زمان و مقادیر مجاز resolution را با مستندات جاری تطبیق دهید.

## سفارش‌ها

### ثبت سفارش

| مشخصه | مقدار |
|---|---|
| مسیر | POST /pro/capi/v1/orders/submit |
| کلاینت | submit_order(...) |
| احراز هویت | توکن و امضای خودکار |

| پارامتر | شرح |
|---|---|
| base_coin_symbol | ارز پایه، مانند BTC |
| quote_coin_symbol | ارز مقابل، مانند IRT یا USDT |
| type | 0 فروش، 1 خرید |
| category_type | طبق بخش ثبت سفارش مستندات: 0 عادی، 1 فوری، 2 حد سود/ضرر، 3 OCO |
| amount | مقدار سفارش؛ در خرید فوری ممکن است لازم نباشد |
| price | قیمت؛ وابسته به نوع سفارش |
| quote_coin_amount | در خرید فوری بر اساس ارز مقابل لازم است |
| stop_price | برای حد سود/ضرر و OCO لازم است |
| trigger_price | برای OCO لازم است |
| trailing_price_condition، activation_price_condition، delta | کلاینت امکان ارسال این فیلدها را می‌دهد؛ کاربردشان به پشتیبانی نوع سفارش در API جاری وابسته است |

نمونهٔ سفارش عادی خرید:

~~~python
result = client.submit_order(
    base_coin_symbol="BTC",
    quote_coin_symbol="IRT",
    type=1,
    category_type=0,
    price="1400000000",
    amount="0.001",
)
~~~

نمونهٔ خرید فوری بر اساس مقدار ارز مقابل:

~~~python
result = client.submit_order(
    base_coin_symbol="BTC",
    quote_coin_symbol="IRT",
    type=1,
    category_type=1,
    quote_coin_amount="500000",
)
~~~

الزامی بودن price و amount به نوع سفارش وابسته است. در مستندات، خرید فوری استثنا دارد: amount ممکن است لازم نباشد و quote_coin_amount لازم است. پاسخ سفارش شامل شناسه، جهت، قیمت، مقدار، وضعیت، مقدار انجام‌شده، کارمزد و اطلاعات جفت‌ارز می‌شود.

### جزئیات سفارش

| مشخصه | مقدار |
|---|---|
| مسیر | GET /pro/capi/v1/orders?id={id} |
| کلاینت | get_order(order_id) |
| پارامتر لازم | id شناسهٔ سفارش |

~~~python
order = client.get_order(123456)["data"]["order"]
~~~

پاسخ شامل جزئیاتی مانند type، each_price، amount، available_amount، done_value، commission، status، category_type و اطلاعات ارزهاست. وضعیت‌های ذکرشده در مستندات شامل 0 در حال تکمیل، 1 تکمیل‌شده، 2 لغوشده و در پاسخ‌های جزئیات 5/6 حالت‌های انتظار شرط/فعال‌سازی هستند.

### لغو سفارش

| مشخصه | مقدار |
|---|---|
| مسیر | POST /pro/capi/v1/orders/cancel |
| کلاینت | cancel_order(order_id=None) |
| پارامتر | order_id اختیاری |

~~~python
# لغو یک سفارش؛ درخواست امضاشده
client.cancel_order(order_id=123456)

# لغو همهٔ سفارش‌های باز؛ طبق مستندات بدون signature
client.cancel_order()
~~~

بدون order_id همهٔ سفارش‌های باز لغو می‌شوند. سفارش‌های فوری قابل لغو نیستند. لغو بدون شناسه را با دقت استفاده کنید.

### تاریخچهٔ سفارش‌ها و معامله‌ها

| مشخصه | مقدار |
|---|---|
| مسیر | GET /pro/capi/v1/orders/get-history |
| کلاینت | get_order_history(is_trade=..., **filters) |
| پارامتر لازم | is_trade: 0 سفارش، 1 معامله |

فیلترهای مستندشده: market_id، type (0 فروش/1 خرید)، category_type، from، to، status و limit. مقدارهای status: 0 باز، 1 تکمیل‌شده، 2 لغوشده. تاریخ نمونه با قالب YYYY-MM-DD است. limit در مستندات با رفتار صفحه‌بندی توضیح داده شده است.

چون from و to نام پارامتر پایتون نیستند، با unpack دیکشنری بفرستید:

~~~python
history = client.get_order_history(
    is_trade=0,
    **{
        "market_id": 19,
        "type": 1,
        "from": "2024-01-01",
        "to": "2024-01-31",
        "status": 0,
    },
)
~~~

خروجی ممکن است صفحه‌بندی‌شده باشد و فیلدهایی مانند total_data، current_page، last_page، per_page و results داشته باشد.

## کیف پول و دارایی

### فهرست دارایی‌ها

| مشخصه | مقدار |
|---|---|
| مسیر | GET /asset/capi/v1/wallet/assets |
| کلاینت | get_assets(**filters) |
| فیلترها | name، alphabet، without_irt، without_zero، page |

- name: فیلتر نام/نماد ارز.
- alphabet: حرف آغاز نام ارز.
- without_irt: 0 همهٔ ارزها؛ 1 حذف ارز تومان.
- without_zero: 0 همه؛ 1 فقط ارزهای دارای موجودی بیشتر از صفر.
- page: شمارهٔ صفحه.

~~~python
assets = client.get_assets(name="BTC", without_zero=1, page=1)["data"]["asset"]
~~~

رکوردها ممکن است شامل شناسه، نام، نماد، موجودی، موجودی قابل استفاده، مقدار در سفارش‌ها، قیمت، درصد تغییر روزانه، ارزش IRT/USDT و بازارهای مرتبط باشند.

### مجموع دارایی‌ها

| مشخصه | مقدار |
|---|---|
| مسیر | GET /asset/capi/v1/wallet/assets/total |
| کلاینت | get_total_assets() |

~~~python
assets_total = client.get_total_assets()["data"]["assets"]
~~~

پاسخ ارزش کل و قابل استفاده را به IRT و USDT ارائه می‌کند و ممکن است بخش‌هایی برای دارایی کل و دارایی والت اصلی داشته باشد.

### تاریخچهٔ کیف پول

| مشخصه | مقدار |
|---|---|
| مسیر | GET /asset/capi/v1/wallet/assets/history |
| کلاینت | get_wallet_history(**filters) |
| فیلترهای نمونهٔ مستندات | type، reason_type، balance_status، from، to، coin_type، symbol |

from و to را با unpack دیکشنری بفرستید:

~~~python
history = client.get_wallet_history(**{
    "type": 0,
    "reason_type": 0,
    "balance_status": 0,
    "from": "2023-01-12",
    "to": "2023-12-12",
    "coin_type": 0,
    "symbol": "BTC",
})
~~~

پاسخ صفحه‌بندی‌شده است. شرح همهٔ مقدارهای مجاز type، reason_type و balance_status در بخش قابل دسترس مستندات روشن نیست؛ پیش از فیلترگذاری به enumها و نمونه‌های تازهٔ پاسخ مراجعه کنید.

## واریز و برداشت

### شبکه‌های واریز

| مشخصه | مقدار |
|---|---|
| مسیر | GET /lite/capi/v1/deposit/networks |
| کلاینت | get_deposit_networks(symbol) |
| پارامتر لازم | symbol؛ مانند TRX |

~~~python
networks = client.get_deposit_networks("TRX")["data"]["networks"]
~~~

شبکه ممکن است شامل network_name، network_layer، description_deposit، min_deposit، address و memo باشد. شبکه و memo را با کیف پول مقصد تطبیق دهید.

### شبکه‌های برداشت

| مشخصه | مقدار |
|---|---|
| مسیر | GET /lite/capi/v1/withdraw/networks |
| کلاینت | get_withdraw_networks(symbol) |
| پارامتر لازم | symbol؛ مانند TRX |

پاسخ شامل موجودی و شبکه‌هاست. دادهٔ هر شبکه می‌تواند network_id، network_name، network_layer، min_withdraw، max_withdraw، fee، address_regex و memo_regex را داشته باشد.

### ثبت درخواست برداشت

| مشخصه | مقدار |
|---|---|
| مسیر | POST /lite/capi/v1/withdraw/submit |
| کلاینت | submit_withdrawal(...) |
| لازم | symbol، network_id، address، value |
| اختیاری | memo |
| احراز هویت | توکن و امضای خودکار |

~~~python
result = client.submit_withdrawal(
    symbol="TRX",
    network_id=486,
    address="آدرس مقصد",
    value="50",
    memo="123456",
)
~~~

این متد درخواست برداشت واقعی ثبت می‌کند. پاسخ موفق به معنی ثبت درخواست در Bit24 است و لزوماً به معنی تأیید یا ارسال نهایی تراکنش روی شبکه نیست.

## خطاها

| کد | معنا |
|---:|---|
| 200 | موفق |
| 400 | درخواست/پارامتر نامعتبر یا خطای تجاری |
| 401 | توکن نامعتبر یا احراز هویت ناموفق |
| 404 | مسیر، بازار یا مورد یافت نشد |
| 422 | اعتبارسنجی پارامتر یا امضا ناموفق |
| 500 | خطای داخلی/موقت سرویس |

~~~python
try:
    response = client.submit_order(
        base_coin_symbol="BTC",
        quote_coin_symbol="IRT",
        type=1,
        category_type=0,
        price="1400000000",
        amount="0.001",
    )
except Bit24HTTPError as exc:
    print("status:", exc.status_code)
    print("message:", str(exc))
    print("full response:", exc.response)
~~~

اگر پاسخ خطا شامل error.errors باشد، کل envelope در exc.response باقی می‌ماند.

## فهرست متدهای کلاینت

| متد | کاربرد | مسیر |
|---|---|---|
| get_markets(page=1, **params) | فهرست بازار | GET /pro/capi/v1/markets |
| get_order_book(base_coin, quote_coin) | دفتر سفارش | GET /pro/capi/v1/markets/order-books |
| get_ohlcv(**params) | کندل OHLC-V | GET /pro/capi/v1/markets/ohclv |
| submit_order(...) | ثبت سفارش امضاشده | POST /pro/capi/v1/orders/submit |
| cancel_order(order_id=None) | لغو یک/همهٔ سفارش‌های باز | POST /pro/capi/v1/orders/cancel |
| get_order(order_id) | جزئیات سفارش | GET /pro/capi/v1/orders |
| get_order_history(is_trade, **filters) | تاریخچهٔ سفارش/معامله | GET /pro/capi/v1/orders/get-history |
| get_assets(**filters) | دارایی‌های کیف پول | GET /asset/capi/v1/wallet/assets |
| get_total_assets() | مجموع دارایی | GET /asset/capi/v1/wallet/assets/total |
| get_wallet_history(**filters) | تاریخچهٔ کیف پول | GET /asset/capi/v1/wallet/assets/history |
| get_deposit_networks(symbol) | اطلاعات شبکه واریز | GET /lite/capi/v1/deposit/networks |
| get_withdraw_networks(symbol) | اطلاعات شبکه برداشت | GET /lite/capi/v1/withdraw/networks |
| submit_withdrawal(...) | ثبت برداشت امضاشده | POST /lite/capi/v1/withdraw/submit |
| create_signature(params, api_secret) | تولید امضای HMAC-SHA256 | محلی |

## ملاحظات ربات خودکار

1. پیش از سفارش، فعال بودن بازار، حداقل سفارش، دقت مجاز ارزها، موجودی قابل استفاده و دفتر سفارش را بررسی کنید.
2. مقادیر مالی را با رشتهٔ اعشاری یا Decimal نگه دارید؛ برای پول از float استفاده نکنید.
3. خطاهای اعتبارسنجی را بدون اصلاح پارامترها پشت سر هم تکرار نکنید.
4. اگر ارسال سفارش یا برداشت timeout شد، نتیجه ممکن است نامعلوم باشد. پیش از ارسال دوباره، تاریخچه یا جزئیات را بررسی کنید تا عملیات تکراری ایجاد نشود.
5. برای فیلدهای پاسخ فرض نکنید همیشه وجود دارند؛ کد را در برابر فیلد اختیاری و تغییر ساختار مقاوم بنویسید.
6. limiter کلاینت بین چند پردازه یا نمونه مشترک نیست؛ نرخ کلی درخواست‌های برنامه را نیز مدیریت کنید.
7. مسیر OHLC-V در مستندات ohclv است؛ همین spelling را نگه دارید مگر مستندات رسمی نسخهٔ جدید مسیر دیگری اعلام کند.

