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

ALLOWED_HOSTS = [
    "ustozhubedu.uz",
    "www.ustozhubedu.uz",
    "localhost",
    "127.0.0.1",
    '*'
]

# Доверенные источники для CSRF (Django 4+ требует схему)
CSRF_TRUSTED_ORIGINS = [
    "https://ustozhubedu.uz",
    "https://www.ustozhubedu.uz",
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
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [TEMPLATES_DIRS,],
        'APP_DIRS': True,
        'OPTIONS': {
            'libraries': {
                'custom_filters': 'teachers.templatetags.custom_filters',
            },
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.i18n',  # ОБЯЗАТЕЛЬНО для i18n
                'teachers.context_processors.unread_messages_count',  # Счётчик непрочитанных сообщений
                'teachers.context_processors.user_conversations_count',  # Счётчик активных переписок
                'teachers.context_processors.unread_notifications_count',  # Счётчик непрочитанных уведомлений
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

# Конфигурация слоёв каналов с Redis backend
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [('127.0.0.1', 6379)],  # Redis сервер
            "capacity": 1500,  # Максимум сообщений в канале
            "expiry": 60,  # Время жизни сообщений (секунды)
        },
    },
}

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

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

# Authentication URLs
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'home'
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
# TELEGRAM BOT SETTINGS
# =============================================================================

# Токен вашего Telegram бота (получите от @BotFather)
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')

# URL вашего сайта (для кнопок и WebApp)
SITE_URL = os.environ.get('SITE_URL', 'https://ustozhubedu.uz')

# URL для Telegram WebApp (будет открываться при нажатии на кнопку в боте)
TELEGRAM_WEBAPP_URL = SITE_URL

# Webhook URL для Telegram (если будете использовать webhook вместо polling)
# TELEGRAM_WEBHOOK_URL = f'{SITE_URL}/api/telegram/webhook/'

# =============================================================================
# ⚡ ОПТИМИЗАЦИЯ: КЭШИРОВАНИЕ
# =============================================================================

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'teacherhub-cache',
        'OPTIONS': {
            'MAX_ENTRIES': 1000,
        }
    }
}

# Время кэширования (в секундах)
CACHE_TTL = 60 * 15  # 15 минут

# =============================================================================
# LOGGING
# =============================================================================

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'teachers': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'telegram_bot': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'allauth': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}
