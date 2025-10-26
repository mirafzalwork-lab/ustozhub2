"""
Django settings for core project.
"""

from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIRS = BASE_DIR / 'templates' 

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-rx4raz1u)-1$v=)b66qio%2hn_pxu(!*g38dfkgfr0kg#+p*br'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

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
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
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
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'

# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Password validation
# Упрощенная валидация паролей - только минимальная длина 4 символа
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 4,
        }
    },
]

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
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

AUTH_USER_MODEL = 'teachers.User'

# Media files (user uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

default_app_config = 'teachers.apps.TeachersConfig'

# =============================================================================
# TELEGRAM BOT SETTINGS
# =============================================================================

# Токен вашего Telegram бота (получите от @BotFather)
TELEGRAM_BOT_TOKEN = '6599919259:AAGqnQXsjUaXoaSjFqisVy-QmRaDdlgVxdI'  # ЗАМЕНИТЕ НА ВАШ ТОКЕН!

# URL вашего сайта (для кнопок и WebApp)
SITE_URL = 'https://ustozhubedu.uz'

# URL для Telegram WebApp (будет открываться при нажатии на кнопку в боте)
TELEGRAM_WEBAPP_URL = f'{SITE_URL}'

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
CACHE_TTL = 60 * 15  # 15 минут для фильтров