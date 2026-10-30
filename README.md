# نانا اکسسوری شاپ — Astro + Tailwind

فروشگاه استاتیک فارسی با طراحی لطیف و فانتزی، مناسب GitHub Pages و دامنه‌ی `nana-accessory.ir`.

## امکانات

- Astro 5 + Tailwind CSS 4؛ خروجی کاملاً استاتیک در `dist/`.
- طراحی RTL و فارسی، واکنش‌گرا برای موبایل و دسکتاپ.
- عنوان و توضیحات اختصاصی، canonical، Open Graph، Twitter Card، JSON-LD از نوع Store، sitemap و robots.txt.
- فهرست محصولات از `src/data/products.json`.
- GitHub Actions برای همگام‌سازی روزانه‌ی محصولات و انتشار خودکار روی GitHub Pages.
- اگر Basalam پاسخ ناقص بدهد یا ساختار صفحه عوض شده باشد، اسکریپت فایل محصولات قبلی را حفظ می‌کند تا سایت ناگهان خالی نشود.

## انتشار روی GitHub Pages

1. تمام فایل‌های این پروژه را در ریشه‌ی یک repository جدید GitHub قرار بده و نام branch اصلی را `main` بگذار.
2. در repository برو به **Settings → Pages → Build and deployment** و گزینه‌ی **Source: GitHub Actions** را انتخاب کن.
3. workflow با هر push و روزی یک بار اجرا می‌شود. اولین اجرا را از **Actions → Build, sync products & deploy → Run workflow** دستی شروع کن.
4. در **Settings → Pages → Custom domain** مقدار `nana-accessory.ir` را وارد کن. فایل `CNAME` هم در ریشه قرار دارد.
5. در DNS دامنه، رکوردهای A دامنه‌ی اصلی را به IPهای GitHub Pages تنظیم کن و برای `www` یک CNAME به `ArmanKianian.github.io` (یا نام کاربری/دامنه‌ی Pages خودت) بگذار. اگر DNS را از Cloudflare مدیریت می‌کنی، برای شروع Proxy را خاموش (DNS only) بگذار.

IPهای رسمی GitHub Pages و راهنمای دامنه‌ی سفارشی را از مستندات رسمی GitHub بررسی کن؛ مقادیر DNS ممکن است تغییر کنند.

## همگام‌سازی Basalam — نکته‌ی مهم

اسکریپت `scripts/sync_basalam.py` در حال حاضر از داده‌های عمومی صفحه‌ی غرفه و metadata محصولات استفاده می‌کند؛ به ورود به حساب یا رمز عبور نیاز ندارد. این روش غیررسمی است و Basalam ممکن است ساختار HTML یا دسترسی عمومی را تغییر دهد. اگر صفحه‌ی غرفه به ربات‌ها پاسخ ندهد، اسکریپت با خطا متوقف می‌شود و کاتالوگ قبلی را دست‌نخورده نگه می‌دارد. اجرای روزانه به‌تنهایی تضمین نمی‌کند که هر محصول جدید/حذف‌شده در تمام شرایط کشف شود.

**راه مطمئن‌تر برای فروشگاه عملیاتی:** استفاده از API رسمی Basalam با دسترسی `vendor.product.read` و توکن فروشنده است. مستندات: https://developers.basalam.com و https://github.com/basalam/openapi-docs . این نسخه برای کارکرد اولیه به توکن نیاز ندارد؛ در صورت دریافت دسترسی API می‌توان منبع داده را به API رسمی تغییر داد.

### اجرای دستی روی سیستم

```bash
python3 -m pip install requests
python3 scripts/sync_basalam.py
npm install
npm run dev
```

برای ساخت خروجی:

```bash
npm run build
```

خروجی نهایی داخل `dist/` ساخته می‌شود.

## شخصی‌سازی

- متن‌ها و چیدمان: `src/pages/index.astro`
- رنگ‌ها و استایل عمومی: `src/styles/global.css`
- لینک‌های شبکه‌های اجتماعی و غرفه: همان فایل صفحه و `src/components/Header.astro`
- کاتالوگ محصولات: `src/data/products.json` (بهتر است خروجی sync را دستی ویرایش نکنی)

## سئوی بیشتر بعد از انتشار

- سایت را در Google Search Console و Bing Webmaster Tools ثبت کن و sitemap را ارسال کن.
- یک تصویر واقعی و باکیفیت `og-cover.svg` با اندازه‌ی پیشنهادی 1200×630 در `public/` قرار بده.
- برای محصولات، عکس واقعی و عنوان فارسی دقیق در باسلام وارد کن؛ عنوان‌های واقعی و توصیفی از تکرار کلمات کلیدی بهترند.
- اطلاعات قیمت/موجودی را در صفحه‌ی باسلام بررسی کن؛ قیمت نهایی و موجودی می‌تواند تغییر کند.
