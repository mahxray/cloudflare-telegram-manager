<a id="top"></a>
<p align="center"><img src="assets/banner.svg" alt="Cloudflare Telegram Manager" width="100%"></p>
<div dir="rtl" align="right">

<h1>☁️ راهنمای نصب و راه‌اندازی Cloudflare Telegram Manager</h1>
<p><strong>Cloudflare Telegram Manager — نسخه ۲.۸.۱</strong></p>
<p>مرجع کامل نصب روی سرور، ساخت ربات تلگرام، اتصال امن حساب Cloudflare، راهبری سرویس، به‌روزرسانی و رفع خطاهای متداول.</p>
<p>🌐 <a href="README_EN.md">English documentation</a> | 🏠 <a href="README.md">صفحه اصلی پروژه</a></p>

<blockquote><strong>توجه:</strong> این پروژه مستقل و غیررسمی است. برای مدیریت حساب‌های خودتان از آن استفاده کنید. فایل‌های دارای رمز و توکن را در مخزن عمومی یا کانال تلگرام منتشر نکنید.</blockquote>

<h2>📚 فهرست راهنما</h2>
<ul>
<li><a href="#features">امکانات</a></li>
<li><a href="#requirements">پیش‌نیازهای سرور</a></li>
<li><a href="#botfather">ساخت ربات و دریافت شناسه مدیر</a></li>
<li><a href="#install">نصب کامل و راه‌اندازی با Docker</a></li>
<li><a href="#cloudflare">ساخت API Token و اتصال حساب Cloudflare</a></li>
<li><a href="#commands">دستورات ربات و مدیریت سرویس</a></li>
<li><a href="#upgrade">آپدیت، بکاپ و نگهداری اطلاعات</a></li>
<li><a href="#troubleshooting">خطاهای رایج و راهکارها</a></li>
<li><a href="#security">نکات امنیتی</a></li>
</ul>

<a id="features"></a>
<h2>✨ امکانات پروژه</h2>
<ul>
<li>مدیریت چند حساب Cloudflare، تغییر حساب فعال و آزمایش اتصال API.</li>
<li>نمایش دامنه‌ها و رکوردهای DNS با صفحه‌بندی؛ ساخت، ویرایش، حذف و جستجوی رکوردها.</li>
<li>ورود فایل BIND و دریافت خروجی DNS؛ پشتیبانی از ۲۱ نوع رکورد تعریف‌شده در پروژه.</li>
<li>مدیریت برخی تنظیمات SSL/TLS و شبکه، پاک‌سازی کش و مدیریت DNSSEC با تأیید عملیات حساس.</li>
<li>ذخیره‌سازی رمزنگاری‌شده توکن‌ها با Fernet، محدودسازی دسترسی با شناسه عددی مدیر و دریافت توکن فقط در چت خصوصی.</li>
<li>منوی فارسی و آموزش داخلی برای اتصال حساب و رفع خطاهای متداول.</li>
</ul>

<a id="requirements"></a>
<h2>🖥️ ۱. پیش‌نیازهای سرور</h2>
<ul>
<li>یک VPS لینوکسی با دسترسی <code>root</code> یا <code>sudo</code>؛ برای نصب خودکار، <strong>Ubuntu</strong> پیشنهاد می‌شود.</li>
<li>اینترنت خروجی به GitHub، رجیستری Docker، <code>api.telegram.org</code> و <code>api.cloudflare.com</code>.</li>
<li>نصب <code>git</code> و <code>python3</code>؛ نصاب در صورت نیاز تلاش می‌کند Docker و Compose را نصب کند.</li>
<li>توکن ربات تلگرام، شناسه عددی مدیر و یک حساب Cloudflare دارای دامنه.</li>
</ul>
<p>ربات از <strong>Long Polling</strong> استفاده می‌کند؛ برای عملکرد عادی آن <strong>نیازی به دامنه، SSL یا بازکردن پورت ورودی نیست</strong>. دسترسی HTTPS خروجی لازم است.</p>
<p>اگر Docker را از قبل نصب کرده‌اید، نسخه و سرویس آن را بررسی کنید:</p>
</div>

~~~bash
docker --version
docker compose version
sudo systemctl status docker --no-pager
~~~

<div dir="rtl" align="right">
<a id="botfather"></a>
<h2>🤖 ۲. ساخت ربات تلگرام و دریافت شناسه مدیر</h2>
<ol>
<li>در تلگرام وارد <a href="https://t.me/BotFather">@BotFather</a> شوید.</li>
<li>دستور <code>/newbot</code> را بفرستید و نام نمایشی و نام کاربری منتهی به <code>bot</code> انتخاب کنید.</li>
<li><strong>Bot Token</strong> ارائه‌شده را در جای امن نگه دارید. آن را در GitHub، اسکرین‌شات یا گروه ارسال نکنید.</li>
<li>شناسه <strong>عددی</strong> حساب تلگرام مدیر (<code>Telegram User ID</code>) را از روشی مطمئن پیدا کنید؛ این مقدار با <code>@username</code> تفاوت دارد.</li>
<li>اگر چند مدیر دارید، شناسه‌ها را با کامای انگلیسی جدا کنید؛ نمونه: <code>123456789,987654321</code>.</li>
</ol>
<p><strong>نکته:</strong> ربات فقط به شناسه‌هایی که در <code>TELEGRAM_ADMIN_IDS</code> تعریف می‌شوند پاسخ می‌دهد.</p>

<a id="install"></a>
<h2>🐳 ۳. نصب کامل روی Ubuntu</h2>
<p>با SSH به سرور متصل شوید. دستورهای زیر ابزارهای اولیه را نصب و پروژه را در <code>/opt/cloudflare-telegram-manager</code> دریافت می‌کنند:</p>
</div>

~~~bash
sudo apt-get update
sudo apt-get install -y git python3 ca-certificates
sudo git clone https://github.com/mahxray/cloudflare-telegram-manager.git /opt/cloudflare-telegram-manager
cd /opt/cloudflare-telegram-manager
sudo bash install.sh
~~~

<div dir="rtl" align="right">
<p>در نصب اولیه، نصاب دو مقدار درخواست می‌کند:</p>
<ol>
<li><code>Telegram Bot Token</code>: توکن دریافتی از BotFather؛ هنگام ورود به‌صورت مخفی دریافت می‌شود.</li>
<li><code>Telegram Admin numeric ID(s)</code>: شناسه عددی مدیر یا چند مدیر (جداشده با کاما).</li>
</ol>
<p>سپس نصاب کلید رمزنگاری را تولید می‌کند، فایل خصوصی <code>.env</code> می‌سازد، دسترسی پوشه داده را تنظیم می‌کند، ایمیج Docker را می‌سازد و سرویس را اجرا می‌کند.</p>
<p>برای بررسی وضعیت، دستورهای زیر را اجرا کنید:</p>
</div>

~~~bash
cd /opt/cloudflare-telegram-manager
sudo docker compose ps
sudo bash diagnose.sh
~~~

<div dir="rtl" align="right">
<p>در خروجی <code>docker compose ps</code> کانتینر <code>cloudflare_telegram_manager</code> باید در وضعیت <code>running</code> یا <code>Up</code> باشد. <code>diagnose.sh</code> وضعیت تنظیمات، دیتابیس، رمزگشایی و ارتباط تلگرام را بدون چاپ مقدار توکن بررسی می‌کند.</p>
<p>اگر نسخه قدیمی Compose دارید که از دستور <code>docker compose</code> پشتیبانی نمی‌کند، در دستورهای مدیریتی از <code>docker-compose</code> استفاده کنید.</p>
<p>حالا در گفت‌وگوی <strong>خصوصی</strong> ربات، دستور <code>/start</code> را ارسال کنید.</p>

<a id="cloudflare"></a>
<h2>☁️ ۴. اتصال حساب Cloudflare</h2>
<h3>مرحله اول: ساخت API Token محدود</h3>
<ol>
<li>وارد <a href="https://dash.cloudflare.com/">Cloudflare Dashboard</a> شوید.</li>
<li>صفحه <a href="https://dash.cloudflare.com/profile/api-tokens">My Profile → API Tokens</a> را باز کنید.</li>
<li><strong>Create Token</strong> را انتخاب کنید. قالب <strong>Edit zone DNS</strong> نقطه شروع مناسبی است؛ برای مجوزهای دقیق‌تر می‌توانید <strong>Custom Token</strong> بسازید.</li>
<li>برای مشاهده دامنه‌ها، مجوز <strong>Zone → Zone → Read</strong> و برای مدیریت رکوردها <strong>Zone → DNS → Edit</strong> را مطابق امکانات موردنیاز تنظیم کنید.</li>
<li>برای قابلیت‌های دیگر، مانند Zone Settings، Cache Purge یا DNSSEC، <strong>فقط مجوز مرتبط</strong> را اضافه کنید؛ بسته به نوع توکن و سطح دسترسی حساب، گزینه‌ها متفاوت‌اند.</li>
<li>در <strong>Zone Resources</strong> دسترسی را تا جای ممکن به همان دامنه‌های موردنظر محدود کنید.</li>
<li>توکن را بسازید و <strong>فقط مقدار خام API Token</strong> را کپی کنید؛ از Global API Key استفاده نکنید.</li>
</ol>
<p>منابع رسمی: <a href="https://developers.cloudflare.com/fundamentals/api/get-started/create-token/">راهنمای ساخت توکن</a> و <a href="https://developers.cloudflare.com/fundamentals/api/reference/permissions/">فهرست مجوزها</a>.</p>

<h3>مرحله دوم: یافتن Account ID</h3>
<p>در داشبورد Cloudflare، حساب مربوط به دامنه‌ها را انتخاب کرده و <strong>Account ID</strong> همان حساب را کپی کنید. <strong>Account ID با Zone ID فرق دارد.</strong> ربات انتظار شناسه ۳۲ کاراکتری هگزادسیمال دارد.</p>

<h3>مرحله سوم: ثبت حساب داخل ربات</h3>
<ol>
<li>در پیوی ربات <code>/start</code> بزنید.</li>
<li>به <strong>👤 حساب‌های Cloudflare ← ➕ افزودن حساب جدید</strong> بروید.</li>
<li>یک نام دلخواه برای حساب وارد کنید.</li>
<li><strong>Account ID</strong> و سپس <strong>API Token</strong> را به ترتیب ارسال کنید.</li>
<li>هنگام درخواست توکن، پیشوند <code>Bearer</code>، کوتیشن یا متن اضافی وارد نکنید؛ خود ربات هدر Authorization را می‌سازد.</li>
<li>پس از اتصال، از صفحه حساب گزینه <strong>🧪 تست اتصال</strong> را بزنید و دامنه‌ها را بررسی کنید.</li>
</ol>
<p>در صورت دریافت خطای <code>403</code>، قبل از هر چیز محدوده Zone Resources و مجوزهای توکن را بررسی کنید. همه امکانات ربات با یک توکن صرفاً DNS قابل استفاده نیستند.</p>

<a id="commands"></a>
<h2>🎛️ ۵. دستورات و مدیریت روزمره</h2>
<ul>
<li><code>/start</code> — بازکردن منوی اصلی</li>
<li><code>/menu</code> — بازگشت به منوی اصلی</li>
<li><code>/language</code> — انتخاب زبان فارسی، انگلیسی یا روسی با دکمه</li>
<li><code>/accounts</code> — مدیریت حساب‌های Cloudflare</li>
<li><code>/help</code> — نمایش راهنمای ربات</li>
<li><code>/cancel</code> — لغو مرحله فعلی</li>
</ul>
<p>🌐 <strong>زبان‌های ربات:</strong> فارسی 🇮🇷، انگلیسی 🇬🇧 و روسی 🇷🇺. برای تغییر زبان، دستور <code>/language</code> یا دکمه «انتخاب زبان» در منوی اصلی را بزنید. زبان هر مدیر جداگانه در دیتابیس ذخیره می‌شود و بعد از ری‌استارت هم حفظ خواهد شد.</p>
<p>فهرست دستورهای منوی تلگرام هنگام راه‌اندازی ربات با همین دستورها هماهنگ می‌شود. پس از به‌روزرسانی و راه‌اندازی مجدد، فرمان قدیمی <code>/id</code> از منوی پیش‌فرض حذف می‌شود؛ این نسخه فرمان <code>/id</code> ندارد.</p>
<ul>
</ul>
<p>مدیریت سرویس از طریق SSH:</p>
</div>

~~~bash
cd /opt/cloudflare-telegram-manager

# Status
sudo docker compose ps

# Restart
sudo docker compose restart cloudflare-bot

# Stop
sudo docker compose stop

# Start again
sudo docker compose up -d

# Sanitized diagnostic
sudo bash diagnose.sh
~~~

<div dir="rtl" align="right">
<p>برای اشتراک‌گذاری خطاها، <strong>گزارش تشخیصی پاک‌سازی‌شده</strong> را ترجیح دهید؛ لاگ کامل ممکن است حاوی داده‌های حساس باشد.</p>

<a id="upgrade"></a>
<h2>🔄 ۶. به‌روزرسانی، بکاپ و نگهداری داده‌ها</h2>
<h3>آپدیت نصب GitHub</h3>
<p>اگر پروژه را با Git نصب کرده‌اید، از همان مسیر فعلی به‌روزرسانی کنید:</p>
</div>

~~~bash
cd /opt/cloudflare-telegram-manager
sudo bash install.sh --update
sudo docker compose ps
~~~

<div dir="rtl" align="right">
<p><strong>مهم:</strong> نصاب قبل از اجرای به‌روزرسانی، از <code>.env</code> و دیتابیس <code>data/bot.db</code> (در صورت وجود) داخل <code>backups/</code> بکاپ می‌گیرد. فایل <code>.env</code>، کلید <code>ENCRYPTION_KEY</code> و داده‌های قبلی را حفظ می‌کند. <strong>کلید قدیمی را عوض نکنید</strong>؛ با عوض‌شدن آن توکن‌های رمزنگاری‌شده قبلی قابل خواندن نخواهند بود.</p>
<h3>اگر نسخه قبلی را با ZIP نصب کرده‌اید</h3>
<p>در همان پوشه نصب قبلی کار کنید؛ ابتدا از <code>.env</code> و <code>data/</code> نسخه پشتیبان بگیرید. سپس سورس جدید را <strong>بدون جایگزین‌کردن این دو مسیر</strong> منتقل کنید و <code>sudo bash install.sh</code> را اجرا کنید. کلون‌کردن پروژه در پوشه تازه، حساب‌های نسخه قبلی را خودکار جابه‌جا نمی‌کند.</p>
<p>بکاپ‌ها شامل رمزها و داده‌های حساس هستند؛ آن‌ها را با دسترسی محدود و خارج از GitHub نگه دارید. برای بازیابی، از روی یک بکاپ سالم، فایل <code>.env</code> و دیتابیس را به مسیر نصب برگردانید، سپس نصاب را اجرا کنید تا مالکیت و مجوزهای لازم اصلاح شوند.</p>

<a id="troubleshooting"></a>
<h2>🛠️ ۷. عیب‌یابی و خطاهای رایج</h2>
<ul>
<li><strong>ربات پاسخ نمی‌دهد:</strong> کانتینر، اتصال خروجی به تلگرام، Bot Token و شناسه عددی مدیر را بررسی کنید؛ سپس <code>sudo bash diagnose.sh</code> را اجرا کنید.</li>
<li><strong>خطای <code>6003</code> یا <code>6111</code>:</strong> معمولاً مشکل فرمت یا اعتبار هدر Authorization است. API Token خام را بدون Bearer و فاصله اضافی وارد کنید؛ در صورت لزوم یک توکن جدید بسازید.</li>
<li><strong>خطای <code>403</code>:</strong> مجوزها، Zone Resources، محدودیت IP و تاریخ انقضای توکن را بررسی کنید.</li>
<li><strong>خطای <code>404</code> یا خالی بودن فهرست دامنه‌ها:</strong> درستی Account ID، مجوز Zone Read و دامنه‌های قابل‌دسترسی را بررسی کنید.</li>
<li><strong>خطای <code>Button_data_invalid</code>:</strong> نسخه فعلی کدگذاری شناسه‌های طولانی را مدیریت می‌کند؛ مطمئن شوید کانتینر با سورس جدید ساخته شده است.</li>
<li><strong>خطای <code>Message is not modified</code>:</strong> فشردن دوباره دکمه نباید خطای قابل‌مشاهده ایجاد کند؛ در صورت تکرار، نسخه نصب‌شده را بررسی کنید.</li>
<li><strong>عدم امکان رمزگشایی حساب:</strong> کلید اصلی <code>ENCRYPTION_KEY</code> را از بکاپ بازیابی کنید؛ دیتابیس را پاک نکنید.</li>
<li><strong>خطای Permission در SQLite:</strong> نصاب مجوز پوشه <code>data/</code> را برای UID کانتینر <code>10001</code> اصلاح می‌کند؛ دوباره نصاب را اجرا کنید.</li>
<li><strong>خطای Docker Compose:</strong> وجود Docker Engine/Compose و دسترسی سرور به رجیستری ایمیج‌ها را بررسی کنید؛ <a href="https://docs.docker.com/engine/install/ubuntu/">مستندات رسمی Docker برای Ubuntu</a> را ببینید.</li>
</ul>
<p>اگر DNS یا وب‌سایت شما سرویس عملیاتی دارد، قبل از تغییر رکوردها، فعال/غیرفعال‌کردن DNSSEC یا پاک‌سازی گسترده کش، اثر آن را بررسی و از رکوردها خروجی تهیه کنید.</p>

<a id="security"></a>
<h2>🔒 ۸. توصیه‌های امنیتی</h2>
<ul>
<li>هرگز <code>.env</code>، <code>data/</code>، <code>backups/</code>، کلید رمزنگاری، توکن یا لاگ خام را در GitHub قرار ندهید.</li>
<li>توکن Cloudflare را با <strong>حداقل مجوز لازم</strong> و محدود به دامنه‌های خودتان بسازید؛ از Global API Key استفاده نکنید.</li>
<li>توکن را فقط در <strong>چت خصوصی</strong> ربات وارد کنید. اگر افشا شد، فوراً از پنل Cloudflare آن را لغو و جایگزین کنید.</li>
<li>برای مدیرها فقط شناسه عددی معتبر را در تنظیمات قرار دهید.</li>
<li>نسخه‌های جدید را ابتدا روی حساب یا دامنه آزمایشی بررسی کنید؛ تست‌های آفلاین جای آزمون سرویس واقعی را نمی‌گیرند.</li>
</ul>
<h2>🧪 ۹. اجرای تست‌های آفلاین</h2>
<p>برای بررسی منطق داخلی، در پوشه پروژه:</p>
</div>

~~~bash
python3 test_auth.py
python3 test_account_connection.py
python3 test_callback_encoding.py
python3 test_interface.py
python3 test_account_commands.py
python3 test_command_menu.py
python3 test_i18n.py
~~~

<div dir="rtl" align="right">
<p>این تست‌ها به توکن واقعی Telegram/Cloudflare نیاز ندارند؛ تأیید اتصال عملی را باید جداگانه و با دسترسی کم‌خطر انجام دهید.</p>
<p>📖 <a href="README_EN.md">Read the English guide</a> | 🏠 <a href="README.md">بازگشت به صفحه اصلی</a> | ⬆️ <a href="#top">بالای صفحه</a></p>
</div>
