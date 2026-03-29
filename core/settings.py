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
    
    # Third-party apps
    'channels',  # Django Channels для WebSocket support
    'formtools',  # Django Form Tools для multi-step forms
    
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

# Media files (user uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

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
    },
}
