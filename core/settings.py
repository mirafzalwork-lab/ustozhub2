"""
Django settings for core project.
"""

import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIRS = BASE_DIR / 'templates'

# Load .env file if it exists
_env_path = BASE_DIR / '.env'
if _env_path.is_file():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, value = line.partition('=')
                os.environ.setdefault(key.strip(), value.strip())

# SECURITY: Secret key from environment variable (no fallback in production)
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "SECRET_KEY environment variable is not set. "
        "Add it to your .env file or set it as an environment variable."
    )

# SECURITY: Debug mode from environment variable (default False in production)
DEBUG = os.environ.get('DEBUG', 'False').lower() in ('true', '1', 'yes')

# Hosts разрешены из env (CSV), плюс обязательные defaults.
# Wildcard '*' убран — это была дыра в безопасности (Host header injection).
# 164.92.185.36 — production droplet (DigitalOcean), доступ по IP до настройки DNS.
_default_hosts = "ustozhubedu.uz,www.ustozhubedu.uz,164.92.185.36,localhost,127.0.0.1"
ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get('ALLOWED_HOSTS', _default_hosts).split(',') if h.strip()
]

# В DEBUG-режиме автоматически разрешаем все локальные хосты для удобства dev.
if DEBUG:
    for h in ('localhost', '127.0.0.1', '0.0.0.0', 'testserver'):
        if h not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(h)

# Доверенные источники для CSRF (Django 4+ требует схему).
# Дополняются из env CSRF_TRUSTED_ORIGINS (CSV).
_default_csrf = "https://ustozhubedu.uz,https://www.ustozhubedu.uz"
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get('CSRF_TRUSTED_ORIGINS', _default_csrf).split(',') if o.strip()
]

# Application definition
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',  # Требуется для django-allauth
    'django.contrib.sitemaps',  # SEO: /sitemap.xml
    'django.contrib.humanize',  # intcomma для форматирования валюты

    # Third-party apps
    'channels',  # Django Channels для WebSocket support
    'formtools',  # Django Form Tools для multi-step forms

    # django-allauth
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',

    # Local apps
    'teachers',
    'billing',
]

# ИСПРАВЛЕНО: правильный порядок middleware для i18n
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',  # Должен быть перед LocaleMiddleware
    'django.middleware.locale.LocaleMiddleware',  # После SessionMiddleware, перед CommonMiddleware
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',  # Требуется для django-allauth
    'teachers.middleware.OnboardingMiddleware',  # Форсит onboarding для пользователей без профиля
    'teachers.middleware.CSPReportOnlyMiddleware',  # CSP в report-only — собираем нарушения, не блокируя
]

ROOT_URLCONF = 'core.urls'

_TEMPLATE_LOADERS = [
    'django.template.loaders.filesystem.Loader',
    'django.template.loaders.app_directories.Loader',
]
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [TEMPLATES_DIRS,],
        # APP_DIRS несовместим с явными loaders. В проде — cached.Loader
        # (шаблон компилируется один раз и кэшируется в памяти процесса),
        # в DEBUG — обычные загрузчики ради авто-перезагрузки.
        'OPTIONS': {
            'libraries': {
                'custom_filters': 'teachers.templatetags.custom_filters',
            },
            'loaders': _TEMPLATE_LOADERS if DEBUG else [
                ('django.template.loaders.cached.Loader', _TEMPLATE_LOADERS),
            ],
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.i18n',  # ОБЯЗАТЕЛЬНО для i18n
                'teachers.context_processors.unread_messages_count',  # Счётчик непрочитанных сообщений
                'teachers.context_processors.user_conversations_count',  # Счётчик активных переписок
                'teachers.context_processors.unread_notifications_count',  # Счётчик непрочитанных уведомлений
                'teachers.context_processors.admin_nav_badges',  # Бейджи админ-навигации (staff)
                'teachers.context_processors.telegram_links',  # Ссылки на Telegram канал/бот (футер и т.д.)
                'teachers.context_processors.telegram_connect',  # Статус привязки + баннер «Подключить» на любой странице
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# =============================================================================
# 🔌 DJANGO CHANNELS & WEBSOCKETS CONFIGURATION
# =============================================================================

# ASGI приложение для WebSocket support
ASGI_APPLICATION = 'core.asgi.application'

# Redis URL — общий для Channels, кэша и Celery.
# Default: localhost:6379. В проде задаётся через env REDIS_URL.
REDIS_URL = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379')

# Конфигурация слоёв каналов с Redis backend
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [REDIS_URL + '/0'],
            "capacity": 1500,  # Максимум сообщений в канале
            "expiry": 60,  # Время жизни сообщений (секунды)
        },
    },
}

# =============================================================================
# DATABASE
# =============================================================================
# Поддерживаются два варианта:
#   • DATABASE_URL=postgres://user:pass@host:port/dbname  (production)
#   • не задан → fallback на SQLite (development)
#
# Парсер написан вручную, чтобы не тащить dj-database-url ради одного URL.

def _parse_database_url(url: str) -> dict:
    """Парсит postgres://user:pass@host:port/dbname в Django DATABASES dict."""
    from urllib.parse import urlparse, unquote
    p = urlparse(url)
    engine_map = {
        'postgres': 'django.db.backends.postgresql',
        'postgresql': 'django.db.backends.postgresql',
        'mysql': 'django.db.backends.mysql',
        'sqlite': 'django.db.backends.sqlite3',
    }
    engine = engine_map.get(p.scheme, p.scheme)
    if engine == 'django.db.backends.sqlite3':
        return {'ENGINE': engine, 'NAME': p.path.lstrip('/') or ':memory:'}
    return {
        'ENGINE': engine,
        'NAME': p.path.lstrip('/'),
        'USER': unquote(p.username) if p.username else '',
        'PASSWORD': unquote(p.password) if p.password else '',
        'HOST': p.hostname or '',
        'PORT': str(p.port) if p.port else '',
        'CONN_MAX_AGE': int(os.environ.get('DB_CONN_MAX_AGE', '60')),
        # Переиспользуем соединения, но проверяем их живость (Django 4.1+),
        # чтобы persistent-коннекты не падали на «stale connection».
        'CONN_HEALTH_CHECKS': True,
        'OPTIONS': {
            # Для PG включаем SSL по умолчанию в проде (отключить можно через env)
            'sslmode': os.environ.get('DB_SSLMODE', 'prefer'),
        } if engine == 'django.db.backends.postgresql' else {},
    }


_database_url = os.environ.get('DATABASE_URL', '').strip()
if _database_url:
    DATABASES = {'default': _parse_database_url(_database_url)}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# В production запрещаем неявный SQLite-fallback. На SQLite select_for_update —
# no-op, а вся БД берёт единый write-lock: это ломает корректность бронирований
# и денежных выплат под конкурентностью (см. deploy/DEPLOY.md). Отказ должен быть
# громким на старте, а не тихой потерей денег под нагрузкой.
if not DEBUG and DATABASES['default']['ENGINE'].endswith('sqlite3'):
    raise ImproperlyConfigured(
        'DATABASE_URL не задан (или указывает на SQLite), но DEBUG=False. '
        'Платёжная и booking-логика требуют PostgreSQL (select_for_update). '
        'Задайте DATABASE_URL=postgres://user:pass@host:5432/dbname.'
    )

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 10,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Security settings for production
if not DEBUG:
    # HTTPS and security headers (only in production)
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    SECURE_HSTS_SECONDS = 63072000  # 2 years
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
    # Django стоит за Nginx — без этого request.is_secure() возвращает False
    # и SECURE_SSL_REDIRECT уходит в бесконечный редирект.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    # Gunicorn слушает unix-сокет, поэтому REMOTE_ADDR пустой. django-ratelimit
    # по умолчанию берёт IP из REMOTE_ADDR и падает с ImproperlyConfigured
    # (→ 500 на POST /login/, /register/). Берём реальный IP из X-Real-IP,
    # который Nginx ВСЕГДА перезаписывает в $remote_addr (клиент подделать не может).
    RATELIMIT_IP_META_KEY = 'HTTP_X_REAL_IP'
else:
    # Development settings
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

# Internationalization
LANGUAGE_CODE = 'ru'  # Язык по умолчанию

LANGUAGES = [
    ('ru', 'Русский'),
    ('uz', 'O\'zbekcha'),
    ('en', 'English'),
]

LOCALE_PATHS = [
    BASE_DIR / 'locale',
]

TIME_ZONE = 'Asia/Tashkent'

USE_I18N = True

USE_L10N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR / 'static'
]
STATIC_ROOT = BASE_DIR / 'staticfiles'

# WhiteNoise configuration for serving static files
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

AUTH_USER_MODEL = 'teachers.User'

# Сессии: храним в кэше (Redis в проде) с фолбэком в БД — убирает SELECT/UPDATE
# django_session на каждый авторизованный запрос, но сохраняет durability.
SESSION_ENGINE = 'django.contrib.sessions.backends.cached_db'
SESSION_CACHE_ALIAS = 'default'

# Authentication URLs
LOGIN_URL = 'login'
# Главная залогиненного пользователя — дашборд (роутится по user_type;
# сам редиректит на онбординг, если профиль не заполнен).
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'home'

# =============================================================================
# DJANGO-ALLAUTH & GOOGLE OAUTH2
# =============================================================================

SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',  # Стандартная аутентификация (login/password)
    'allauth.account.auth_backends.AuthenticationBackend',  # allauth (Google и др.)
]

# Настройки allauth
ACCOUNT_LOGIN_BY_CODE_ENABLED = False
ACCOUNT_EMAIL_VERIFICATION = 'none'  # Не требуем верификацию email через allauth (Google уже верифицирует)
ACCOUNT_LOGIN_METHODS = {'email', 'username'}  # Вход по email или username
ACCOUNT_SIGNUP_FIELDS = ['email*', 'password1*']  # email обязателен, пароль обязателен
ACCOUNT_UNIQUE_EMAIL = True

# Social account settings
SOCIALACCOUNT_AUTO_SIGNUP = True  # Автоматически создавать аккаунт при первом входе через Google
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True  # Привязывать Google к существующему аккаунту по email
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_LOGIN_ON_GET = True  # Не показывать промежуточную страницу подтверждения
SOCIALACCOUNT_ADAPTER = 'teachers.adapters.SocialAccountAdapter'

# Google OAuth2 провайдер
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': [
            'profile',
            'email',
        ],
        'AUTH_PARAMS': {
            'access_type': 'online',
        },
        'OAUTH_PKCE_ENABLED': True,
        'FETCH_USERINFO': True,
        'APP': {
            'client_id': os.environ.get('GOOGLE_CLIENT_ID', ''),
            'secret': os.environ.get('GOOGLE_CLIENT_SECRET', ''),
        },
    }
}

# Media files (user uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Upload limits
DATA_UPLOAD_MAX_MEMORY_SIZE = 52428800  # 50MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 52428800  # 50MB

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# =============================================================================
# S3-COMPATIBLE STORAGE (Cloudflare R2 / Amazon S3)
# Используется для прямой загрузки видео через presigned URL
# =============================================================================

S3_ACCESS_KEY_ID = os.environ.get('S3_ACCESS_KEY_ID', '')
S3_SECRET_ACCESS_KEY = os.environ.get('S3_SECRET_ACCESS_KEY', '')
S3_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME', '')
S3_ENDPOINT_URL = os.environ.get('S3_ENDPOINT_URL', '')  # Для R2: https://<account_id>.r2.cloudflarestorage.com
S3_REGION = os.environ.get('S3_REGION', 'auto')
S3_PUBLIC_URL = os.environ.get('S3_PUBLIC_URL', '')  # Публичный URL бакета (CDN или R2 public domain)

# Ограничения для видео-визитки
VIDEO_MAX_SIZE_MB = 50
VIDEO_MAX_DURATION_SECONDS = 90
VIDEO_ALLOWED_CONTENT_TYPES = ['video/mp4']
VIDEO_PRESIGNED_URL_EXPIRY = 600  # 10 минут на загрузку

# =============================================================================
# BILLING & PAYMENTS
# =============================================================================
from decimal import Decimal  # noqa: E402

# Валюта платформы. Денежные суммы храним в DecimalField(14, 2).
BILLING_CURRENCY = 'UZS'

# Комиссия платформы с каждого payout'а учителю (0..1, доля).
# Snapshot этого значения пишется в Subscription.commission_rate в момент покупки —
# изменение этой константы НЕ влияет на уже активные подписки.
PLATFORM_COMMISSION_RATE = Decimal(os.environ.get('PLATFORM_COMMISSION_RATE', '0.15'))

# Окно в часах между end_at урока и автоматическим payout учителю.
# В это окно ученик может открыть dispute и заморозить выплату.
PAYOUT_GRACE_HOURS = int(os.environ.get('PAYOUT_GRACE_HOURS', '6'))

# Доля длительности урока, которую КАЖДАЯ сторона и их одновременное присутствие
# (overlap) должны реально провести в видеокомнате, чтобы урок засчитался
# проведённым. Основной анти-фрод критерий: «зашёл на пару секунд» / «были в
# комнате не одновременно» не должны давать completed (см. Booking.settle_after_end).
LESSON_MIN_PRESENCE_RATIO = float(os.environ.get('LESSON_MIN_PRESENCE_RATIO', '0.4'))

# Минимальная сумма для запроса вывода средств учителем.
MIN_WITHDRAWAL_AMOUNT = Decimal(os.environ.get('MIN_WITHDRAWAL_AMOUNT', '100000.00'))

# Бесплатных переносов в месяц внутри подписки (для ученика).
SUBSCRIPTION_FREE_RESCHEDULES_PER_MONTH = int(
    os.environ.get('SUBSCRIPTION_FREE_RESCHEDULES_PER_MONTH', '2')
)

# Минимальный запас (в часах) до начала урока, в который ещё можно перенести бронь.
RESCHEDULE_MIN_LEAD_HOURS = int(os.environ.get('RESCHEDULE_MIN_LEAD_HOURS', '4'))

# Порог (в часах) до начала урока для полного возврата при отмене урока подписки.
# Отмена раньше порога → урок возвращается в квоту; позже → списывается учителю.
CANCELLATION_FULL_REFUND_HOURS = int(
    os.environ.get('CANCELLATION_FULL_REFUND_HOURS', '24')
)

# --- Анти-обход платформы (v2 Шаг 7) ---
# Маскировать контакты (телефоны/ссылки/@) в чате, пока в паре (ученик, учитель)
# меньше этого числа оплаченных уроков. 0 — отключить маскировку.
CONTACT_MASK_MIN_PAID_LESSONS = int(
    os.environ.get('CONTACT_MASK_MIN_PAID_LESSONS', '5')
)
# Разрешать ли учителю задавать внешнюю видеоссылку (Zoom/Meet). По умолчанию
# нет — только своя Jitsi-комната (защита от обхода + корректный детект неявок).
ALLOW_EXTERNAL_MEETING_URLS = (
    os.environ.get('ALLOW_EXTERNAL_MEETING_URLS', 'false').lower() == 'true'
)

# Минимальное число месяцев в подписке.
SUBSCRIPTION_MIN_MONTHS = int(os.environ.get('SUBSCRIPTION_MIN_MONTHS', '1'))

# Кошелёк платформы (для приёма комиссии). Создаётся миграцией data-migration
# или management-командой; идентифицируется по username.
PLATFORM_ACCOUNT_USERNAME = os.environ.get('PLATFORM_ACCOUNT_USERNAME', '__platform__')

# =============================================================================
# TELEGRAM BOT SETTINGS
# =============================================================================

# Токен вашего Telegram бота (получите от @BotFather)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')

# Публичные точки входа в Telegram (используются в футере, баннерах, онбординге).
# Username без @. URL собираем здесь, чтобы шаблоны не хардкодили ссылки.
TELEGRAM_BOT_USERNAME = os.environ.get('TELEGRAM_BOT_USERNAME', 'ustozhub_bot')
TELEGRAM_BOT_URL = f"https://t.me/{TELEGRAM_BOT_USERNAME}"
TELEGRAM_CHANNEL_USERNAME = os.environ.get('TELEGRAM_CHANNEL_USERNAME', 'UstozHubUz')
TELEGRAM_CHANNEL_URL = f"https://t.me/{TELEGRAM_CHANNEL_USERNAME}"

# Канал для авто-публикации новых преподавателей (бот должен быть его админом).
# Приватный канал → числовой id (-100…), публичный → '@username'.
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID', '-1002986846695')

# URL вашего сайта (для кнопок и WebApp)
SITE_URL = os.environ.get('SITE_URL', 'https://ustozhubedu.uz')

# URL для Telegram WebApp (будет открываться при нажатии на кнопку в боте)
TELEGRAM_WEBAPP_URL = SITE_URL

# Webhook URL для Telegram (если будете использовать webhook вместо polling)
# TELEGRAM_WEBHOOK_URL = f'{SITE_URL}/api/telegram/webhook/'

# =============================================================================
# ⚡ ОПТИМИЗАЦИЯ: КЭШИРОВАНИЕ
# =============================================================================

# Кэш: Redis в production (шарится между Gunicorn и Daphne),
# LocMemCache в dev (если REDIS_URL не задан или USE_REDIS_CACHE=False).
# КРИТИЧНО для multi-process: при LocMem кэш у каждого процесса свой,
# инвалидация не доходит → stale data в WebSocket-процессе.

# В production кэш ВСЕГДА Redis (REDIS_URL всегда определён — его уже используют
# Channels и Celery). Раньше требовался явный env REDIS_URL/USE_REDIS_CACHE, иначе
# прод молча падал на LocMemCache → рассинхрон бейджей между Gunicorn и Daphne.
_use_redis_cache = os.environ.get('USE_REDIS_CACHE', '').lower() in ('true', '1', 'yes')
_disable_redis_cache = os.environ.get('USE_REDIS_CACHE', '').lower() in ('false', '0', 'no')
if _use_redis_cache or (not DEBUG and not _disable_redis_cache):
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.redis.RedisCache',
            'LOCATION': f'{REDIS_URL}/1',  # DB 1 — кэш (DB 0 — Channels, DB 2 — Celery)
            'KEY_PREFIX': 'ustozhub',
            'TIMEOUT': 300,
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'teacherhub-cache',
            'OPTIONS': {'MAX_ENTRIES': 1000},
        }
    }

# Время кэширования (в секундах)
CACHE_TTL = 60 * 15  # 15 минут

# =============================================================================
# ⏰ CELERY (асинхронные задачи и расписание)
# =============================================================================
# Используется для:
#   • scheduled-уведомлений (T-24h / T-3h / T-10min до урока)
#   • очистки протухших booking-холдов
#   • email-рассылок
#   • daily reminder bot
#
# В dev можно гонять задачи синхронно через CELERY_TASK_ALWAYS_EAGER=True.

CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', f'{REDIS_URL}/2')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', f'{REDIS_URL}/3')
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 5 * 60          # hard limit 5 min
CELERY_TASK_SOFT_TIME_LIMIT = 4 * 60     # soft limit 4 min
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
# DatabaseScheduler подключим в Phase 4 (когда django_celery_beat будет установлен).
# Пока используем дефолтный файловый PersistentScheduler.
# CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

# В dev — eager mode выключает реальный брокер (задачи бегут синхронно).
CELERY_TASK_ALWAYS_EAGER = os.environ.get('CELERY_TASK_ALWAYS_EAGER', '').lower() in ('true', '1', 'yes')
CELERY_TASK_EAGER_PROPAGATES = True

# =============================================================================
# 🔎 SUBJECT SEARCH LOG
# =============================================================================
# Логирование поисковых запросов для аналитики. Чтобы не раздувать БД при
# каждом нажатии клавиши в автокомплите, применяется дедупликация (TTL)
# и опциональное сэмплирование.

SEARCH_LOG_ENABLED = True          # глобальный выключатель
SEARCH_LOG_DEDUP_TTL = 300         # один (user|ip, query) пишем не чаще раза в N сек
SEARCH_LOG_SAMPLE_RATE = 1.0       # 1.0 = писать всё, 0.1 = писать 10% запросов

# =============================================================================
# 📝 WIZARD DRAFTS
# =============================================================================
# Сколько дней хранить черновик регистрации учителя в БД (WizardDraft).
WIZARD_DRAFT_TTL_DAYS = 14

# =============================================================================
# 🎥 VIDEO MEETINGS (Jitsi)
# =============================================================================
# База для авто-генерации видео-комнаты на подтверждённую бронь.
# По умолчанию публичный meet.jit.si (бесплатно, без ключей). Можно указать
# свой self-hosted Jitsi через переменную окружения JITSI_BASE_URL.
JITSI_BASE_URL = os.environ.get('JITSI_BASE_URL', 'https://meet.jit.si').rstrip('/')
# Префикс имени комнаты — чтобы комнаты не путались с чужими на публичном сервере.
JITSI_ROOM_PREFIX = os.environ.get('JITSI_ROOM_PREFIX', 'UstozHub')

# Прод работает на self-hosted Jitsi (meet.ustozhubedu.uz). Публичный meet.jit.si
# — это лобби, membersOnly-ошибки и посторонние по угаданному имени комнаты, т.е.
# «видео плохо работает». Если на не-DEBUG окружении переменная окружения слетела
# и мы откатились на публичный сервер — громко предупреждаем в лог, а не молчим.
if not DEBUG and JITSI_BASE_URL == 'https://meet.jit.si':
    import logging as _logging
    _logging.getLogger('django').warning(
        'JITSI_BASE_URL не задан в окружении — используется публичный meet.jit.si. '
        'Уроки могут работать нестабильно (лобби/membersOnly). Проверьте .env на сервере.'
    )

# =============================================================================
# 🎓 LESSON LIFECYCLE (ТЗ: проведение уроков)
# =============================================================================
# За сколько минут до начала открывается комната и активируется кнопка
# «Присоединиться к уроку» (ТЗ §1 — 10 минут).
LESSON_JOIN_LEAD_MINUTES = int(os.environ.get('LESSON_JOIN_LEAD_MINUTES', '10'))
# Сколько минут после конца урока комната ещё доступна (на случай обрыва связи).
# Единый источник: и серверная проверка (lesson_room/attendance), и кнопка в UI.
LESSON_JOIN_GRACE_MINUTES = int(os.environ.get('LESSON_JOIN_GRACE_MINUTES', '30'))
# Через сколько минут после начала урока ученик может САМ отметить неявку
# преподавателя (если тот объективно не подключался к нашей видеокомнате) и
# сразу получить возврат — не дожидаясь Celery settle_after_end (end_at+30).
# Порог защищает преподавателя от штрафа за небольшое опоздание.
TEACHER_NO_SHOW_REPORT_AFTER_MINUTES = int(
    os.environ.get('TEACHER_NO_SHOW_REPORT_AFTER_MINUTES', '15'))
# Неявка ученика (ТЗ §6): первые N неявок за окно «прощаются» — урок
# возвращается ученику (escrow не трогаем, учителю не платим). Начиная с
# (N+1)-й — урок списывается и оплачивается учителю.
STUDENT_NO_SHOW_FORGIVE_LIMIT = int(os.environ.get('STUDENT_NO_SHOW_FORGIVE_LIMIT', '3'))
# Окно подсчёта неявок ученика в днях (ТЗ §6 — за последние 90 дней).
STUDENT_NO_SHOW_WINDOW_DAYS = int(os.environ.get('STUDENT_NO_SHOW_WINDOW_DAYS', '90'))

# Материалы урока (LessonFile): прямая загрузка в S3/R2 через presigned URL.
# Белый список форматов — только безопасные учебные документы/изображения,
# без исполняемых типов. Лимит размера защищает хранилище.
LESSON_FILE_MAX_SIZE_MB = int(os.environ.get('LESSON_FILE_MAX_SIZE_MB', '25'))
LESSON_FILE_ALLOWED_TYPES = {
    'application/pdf': 'pdf',
    'image/png': 'png',
    'image/jpeg': 'jpg',
    'image/gif': 'gif',
    'image/webp': 'webp',
    'text/plain': 'txt',
    'application/msword': 'doc',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    'application/vnd.ms-powerpoint': 'ppt',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
    'application/vnd.ms-excel': 'xls',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'application/zip': 'zip',
}
# Сколько секунд живёт presigned-ссылка на загрузку файла урока.
LESSON_FILE_PRESIGNED_URL_EXPIRY = int(os.environ.get('LESSON_FILE_PRESIGNED_URL_EXPIRY', '600'))

# =============================================================================
# LOGGING
# =============================================================================

_LOG_DIR = BASE_DIR / 'logs'
_LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(_LOG_DIR / 'ustozhub.log'),
            'maxBytes': 10 * 1024 * 1024,   # 10 MB
            'backupCount': 5,
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'WARNING',
            'propagate': False,
        },
        'teachers': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'telegram_bot': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'allauth': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'WARNING',
            'propagate': False,
        },
        'celery': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# =============================================================================
# 🛰 SENTRY (опционально, только если задан DSN)
# =============================================================================
_sentry_dsn = os.environ.get('SENTRY_DSN', '').strip()
if _sentry_dsn:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=_sentry_dsn,
            integrations=[
                DjangoIntegration(),
                CeleryIntegration(),
                LoggingIntegration(level=None, event_level=None),  # logging → breadcrumbs only
            ],
            traces_sample_rate=float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.05')),
            send_default_pii=False,
            environment=os.environ.get('SENTRY_ENV', 'production' if not DEBUG else 'development'),
            release=os.environ.get('SENTRY_RELEASE', ''),
        )
    except ImportError:
        # sentry_sdk не установлен — это норма для dev
        pass

# =============================================================================
# 📧 EMAIL
# =============================================================================
# В dev — пишем в консоль. В проде — настоящий SMTP через env.
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend' if DEBUG
    else 'django.core.mail.backends.smtp.EmailBackend',
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True').lower() in ('true', '1', 'yes')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'UstozHub <noreply@ustozhubedu.uz>')
SERVER_EMAIL = os.environ.get('SERVER_EMAIL', DEFAULT_FROM_EMAIL)

# Базовый абсолютный URL сайта — нужен, чтобы строить полные ссылки в письмах
# (action_url у Notification хранится относительным, '/billing/...').
SITE_BASE_URL = os.environ.get('SITE_BASE_URL', 'https://ustozhubedu.uz').rstrip('/')

# Дублировать персональные in-app уведомления на email (можно выключить через env).
NOTIFY_EMAIL_ENABLED = os.environ.get('NOTIFY_EMAIL_ENABLED', 'True').lower() in ('true', '1', 'yes')

# =============================================================================
# 💳 WALLET TOPUP (manual flow — до интеграции Payme/Click)
# =============================================================================
# Реквизиты для ручного пополнения через перевод на карту.
# Студент видит их на /billing/my/wallet/topup/, переводит, скриншот в Telegram,
# админ начисляет через /admin/billing/wallet/<user_id>/topup/.
# ВАЖНО: по умолчанию ПУСТО. Фейковый номер-заглушка раньше показывался
# пользователям, если env не задан в проде → деньги уходили "в никуда".
# Пустое значение → страница пополнения покажет «временно недоступно».
TOPUP_CARD_NUMBER = os.environ.get('TOPUP_CARD_NUMBER', '')
TOPUP_CARD_HOLDER = os.environ.get('TOPUP_CARD_HOLDER', 'USTOZHUB PAY')
TOPUP_BANK_NAME = os.environ.get('TOPUP_BANK_NAME', 'Uzcard / Humo')
TOPUP_TELEGRAM_HANDLE = os.environ.get('TOPUP_TELEGRAM_HANDLE', 'ustozhub_pay')
TOPUP_SUPPORT_PHONE = os.environ.get('TOPUP_SUPPORT_PHONE', '+998 90 000 00 00')
TOPUP_PROCESSING_HOURS = os.environ.get('TOPUP_PROCESSING_HOURS', '1-2')

# =============================================================================
# 💳 MULTICARD (платёжный шлюз — онлайн-пополнение кошелька)
# =============================================================================
# Документация: https://docs.multicard.uz/
# Поток: создаём инвойс (POST /payment/invoice) → редиректим клиента на
# checkout_url → Multicard шлёт callback на MULTICARD_CALLBACK_URL → зачисляем
# в кошелёк (DEPOSIT) идемпотентно. Суммы в API — в ТИЙИНАХ (1 сум = 100 тийин).
#
# Sandbox:  https://dev-mesh.multicard.uz   (тестовая карта 8600533364098829/2806, OTP 112233)
# Prod:     https://mesh.multicard.uz
MULTICARD_BASE_URL = os.environ.get('MULTICARD_BASE_URL', 'https://dev-mesh.multicard.uz').rstrip('/')
MULTICARD_APPLICATION_ID = os.environ.get('MULTICARD_APPLICATION_ID', '')
MULTICARD_SECRET = os.environ.get('MULTICARD_SECRET', '')
MULTICARD_STORE_ID = os.environ.get('MULTICARD_STORE_ID', '')
# Включатель онлайн-оплаты на странице пополнения (по умолчанию — по наличию ключей).
MULTICARD_ENABLED = (
    os.environ.get('MULTICARD_ENABLED', '').lower() in ('true', '1', 'yes')
    or bool(MULTICARD_APPLICATION_ID and MULTICARD_SECRET and MULTICARD_STORE_ID)
)
# IP, с которого Multicard шлёт callback (для опционального whitelisting во view).
MULTICARD_CALLBACK_IP = os.environ.get('MULTICARD_CALLBACK_IP', '195.158.26.90')
MULTICARD_HTTP_TIMEOUT = int(os.environ.get('MULTICARD_HTTP_TIMEOUT', '20'))
# Лимиты суммы онлайн-пополнения (в сумах).
MULTICARD_MIN_TOPUP = int(os.environ.get('MULTICARD_MIN_TOPUP', '1000'))
MULTICARD_MAX_TOPUP = int(os.environ.get('MULTICARD_MAX_TOPUP', '10000000'))
# Фискальные данные (ofd) для строки чека «Пополнение кошелька».
# MXIK и код упаковки выдаёт налоговая/Multicard под конкретную услугу.
MULTICARD_OFD_MXIK = os.environ.get('MULTICARD_OFD_MXIK', '')
MULTICARD_OFD_PACKAGE_CODE = os.environ.get('MULTICARD_OFD_PACKAGE_CODE', '')
MULTICARD_OFD_NAME = os.environ.get('MULTICARD_OFD_NAME', 'Пополнение кошелька UstozHub')
MULTICARD_OFD_VAT_PERCENT = int(os.environ.get('MULTICARD_OFD_VAT_PERCENT', '0'))
