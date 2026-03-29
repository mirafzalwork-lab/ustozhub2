# UstozHub - Полная техническая документация проекта

## Содержание

1. [Обзор проекта](#1-обзор-проекта)
2. [Технологический стек](#2-технологический-стек)
3. [Структура файлов](#3-структура-файлов)
4. [Конфигурация (settings.py)](#4-конфигурация)
5. [Модели базы данных (21 модель)](#5-модели-базы-данных)
6. [URL-маршрутизация (97 маршрутов)](#6-url-маршрутизация)
7. [Views - представления (40+ функций)](#7-views---представления)
8. [Формы (40+ классов)](#8-формы)
9. [WebSocket consumers](#9-websocket-consumers)
10. [Telegram-бот и уведомления](#10-telegram-бот-и-уведомления)
11. [Фоновые задачи (Celery)](#11-фоновые-задачи-celery)
12. [Сигналы Django](#12-сигналы-django)
13. [Кэширование](#13-кэширование)
14. [Фронтенд и шаблоны](#14-фронтенд-и-шаблоны)
15. [Админ-панель](#15-админ-панель)
16. [Безопасность](#16-безопасность)
17. [Бизнес-логика и пользовательские сценарии](#17-бизнес-логика-и-пользовательские-сценарии)
18. [Деплой и инфраструктура](#18-деплой-и-инфраструктура)
19. [Статистика проекта](#19-статистика-проекта)

---

## 1. Обзор проекта

**UstozHub** (ustozhubedu.uz) — это полнофункциональная веб-платформа для поиска и подбора репетиторов в Узбекистане. Платформа соединяет учителей и учеников, предоставляя инструменты для поиска, общения, оценки и управления образовательными услугами.

### Ключевые возможности

| Функция | Описание |
|---------|----------|
| **Маркетплейс учителей** | Поиск, фильтрация и просмотр профилей учителей с рейтингами |
| **Профили учеников** | Ученики создают профили с интересами и бюджетом |
| **Реальное время (чат)** | WebSocket-мессенджер между учителями и учениками |
| **Уведомления** | Push-уведомления через WebSocket + Telegram-бот |
| **Модерация** | Одобрение/отклонение профилей учителей администрацией |
| **Система отзывов** | Рейтинги по категориям (знания, коммуникация, пунктуальность) |
| **Избранное** | Двусторонняя система закладок (учитель ↔ ученик) |
| **Telegram-бот** | Бот для уведомлений и взаимодействия вне сайта |
| **Аналитика** | Отслеживание просмотров, поисковых запросов, статистика |
| **Многоязычность** | Русский, узбекский, английский |
| **Многошаговая регистрация** | 6-шаговый визард для регистрации учителей |

---

## 2. Технологический стек

### Backend
| Технология | Версия | Назначение |
|-----------|--------|-----------|
| **Python** | 3.14 | Язык программирования |
| **Django** | 5.1+ | Веб-фреймворк |
| **Django Channels** | 4.0.0 | WebSocket-поддержка |
| **Daphne** | 4.2.1 | ASGI-сервер |
| **Redis** | — | Channel Layer + кэш брокера |
| **SQLite3** | — | База данных (dev) |
| **Celery** | — | Фоновые задачи |
| **python-telegram-bot** | 21.0.1 | Telegram Bot API |
| **Pillow** | 10.4+ | Обработка изображений |
| **WhiteNoise** | 6.5.0 | Раздача статики в production |
| **formtools** | — | Многошаговые формы |

### Frontend
| Технология | Назначение |
|-----------|-----------|
| **Django Templates** | Шаблонизатор |
| **Vanilla JavaScript** | Интерактивность (без фреймворков) |
| **Fetch API** | AJAX-запросы |
| **CSS Variables** | Дизайн-система |
| **Font Awesome 6** | Иконки |
| **Google Fonts (Inter)** | Типографика |

### Инфраструктура
| Компонент | Назначение |
|-----------|-----------|
| **Redis** | WebSocket channel layer, кэш |
| **Daphne** | ASGI-сервер (WebSocket + HTTP) |
| **WhiteNoise** | Статические файлы в production |
| **Celery + Beat** | Обработка очереди уведомлений |

---

## 3. Структура файлов

```
ustozhubuz/
├── core/                              # Конфигурация Django-проекта
│   ├── __init__.py
│   ├── settings.py                    # Настройки (~300 строк)
│   ├── urls.py                        # Корневая маршрутизация
│   ├── asgi.py                        # ASGI конфиг (Channels + WebSocket)
│   └── wsgi.py                        # WSGI конфиг (production)
│
├── teachers/                          # Главное приложение
│   ├── models.py                      # 21 модель (~1584 строки)
│   ├── views.py                       # 40+ views (~2144 строки)
│   ├── forms.py                       # Формы профилей (~1122 строки)
│   ├── registration_forms.py          # Формы регистрации (6 шагов)
│   ├── registration_wizard.py         # Визард регистрации учителя
│   ├── urls.py                        # URL-маршруты приложения
│   ├── admin.py                       # Кастомизация админ-панели
│   ├── consumers.py                   # WebSocket consumers (чат + уведомления)
│   ├── routing.py                     # WebSocket маршруты
│   ├── signals.py                     # Django-сигналы
│   ├── context_processors.py          # Контекст для шаблонов (бейджи)
│   ├── telegram_views.py              # API для Telegram
│   ├── admin_telegram_service.py      # Сервис рассылок Telegram
│   ├── apps.py                        # Конфигурация приложения
│   ├── templatetags/                  # Пользовательские фильтры
│   │   ├── custom_filters.py
│   │   └── form_filter.py
│   ├── management/commands/           # Management-команды
│   │   ├── load_subject_categories.py # Загрузка категорий предметов
│   │   ├── process_notifications.py   # Обработка очереди уведомлений
│   │   ├── telegram_users.py          # Управление Telegram-пользователями
│   │   └── update_rankings.py         # Пересчет рейтингов
│   └── migrations/                    # 19 миграций
│
├── telegram_bot/                      # Модуль Telegram-бота
│   ├── bot.py                         # Бот: команды и обработчики
│   ├── notifications.py               # Простой сервис уведомлений
│   ├── notification_service.py        # Продвинутый сервис (очередь, retry)
│   ├── tasks.py                       # Celery-задачи
│   └── __init__.py
│
├── templates/                         # HTML-шаблоны (43 файла)
│   ├── base.html                      # Базовый шаблон (navbar, footer)
│   ├── login.html                     # Страница входа
│   ├── logout.html                    # Страница выхода
│   ├── logic/                         # Основные страницы (~22 шаблона)
│   ├── registration/                  # Шаги регистрации (6 шаблонов)
│   ├── admin/                         # Админ-страницы (6 шаблонов)
│   └── notifications/                 # Уведомления (2 шаблона)
│
├── static/                            # Статические файлы
│   ├── css/registration.css           # Стили регистрации (24KB)
│   ├── js/registration.js             # JS регистрации (15KB)
│   ├── logo/                          # Логотипы
│   └── teachers/admin.css             # Стили админки
│
├── staticfiles/                       # Собранная статика (production)
├── media/                             # Загрузки пользователей
│   ├── avatars/                       # Аватарки
│   └── certificates/                  # Сертификаты учителей
│
├── locale/                            # Переводы (i18n)
│   ├── ru/                            # Русский
│   ├── uz/                            # Узбекский
│   └── en/                            # Английский
│
├── manage.py                          # Django manage
├── requirements.txt                   # Зависимости Python
├── Pipfile / Pipfile.lock             # Pipenv
├── .env.example                       # Шаблон переменных окружения
├── db.sqlite3                         # База данных (dev)
└── dump.rdb                           # Дамп Redis
```

---

## 4. Конфигурация

### Основные настройки (core/settings.py)

**Установленные приложения:**
- `daphne` — ASGI-сервер
- `channels` — WebSocket-поддержка
- `formtools` — многошаговые формы
- `teachers` — основное приложение

**Middleware (порядок):**
1. `SecurityMiddleware` — заголовки безопасности
2. `WhiteNoiseMiddleware` — раздача статики
3. `SessionMiddleware` — сессии
4. `LocaleMiddleware` — i18n
5. `CommonMiddleware` — общие утилиты
6. `CsrfViewMiddleware` — CSRF-защита
7. `AuthenticationMiddleware` — аутентификация
8. `MessageMiddleware` — flash-сообщения
9. `XFrameOptionsMiddleware` — защита от clickjacking

**База данных:**
- Engine: SQLite3 (development)
- Файл: `db.sqlite3`

**Channel Layer (WebSocket):**
```python
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [('127.0.0.1', 6379)],
            "capacity": 1500,
            "expiry": 60,
        },
    },
}
```

**Кэш:**
```python
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'teacherhub-cache',
        'OPTIONS': {'MAX_ENTRIES': 1000}
    }
}
CACHE_TTL = 300        # 5 минут
CACHE_TTL_SHORT = 60   # 1 минута
CACHE_TTL_LONG = 3600  # 1 час
```

**Интернационализация:**
- Язык по умолчанию: `ru` (русский)
- Поддерживаемые: `ru`, `uz`, `en`
- Часовой пояс: `Asia/Tashkent`

**Переменные окружения (.env):**
```
SECRET_KEY=...
DEBUG=False
TELEGRAM_BOT_TOKEN=...
SITE_URL=https://ustozhubedu.uz
```

---

## 5. Модели базы данных

Все модели находятся в `teachers/models.py`. Всего **21 модель**.

---

### 5.1 User (расширяет AbstractUser)

Кастомная модель пользователя.

| Поле | Тип | Описание |
|------|-----|----------|
| `user_type` | CharField(10) | `'student'` / `'teacher'` |
| `phone` | CharField(20) | Телефон |
| `age` | PositiveIntegerField | Возраст (10-100) |
| `gender` | CharField(10) | `'male'` / `'female'` |
| `avatar` | ImageField | Фото профиля (до 300x300px) |
| `is_verified` | BooleanField | Верифицирован ли |
| `created_at` | DateTimeField | Дата создания |
| `updated_at` | DateTimeField | Дата обновления |

**Методы:** `save()` — ресайз аватарки до 300x300px.

---

### 5.2 SubjectCategory

Категории предметов (например, "Точные науки", "Языки").

| Поле | Тип | Описание |
|------|-----|----------|
| `name` | CharField(100, unique) | Название |
| `description` | TextField | Описание |
| `icon` | CharField(50) | CSS-класс иконки |
| `color` | CharField(7) | HEX-цвет (по умолч. `#3B82F6`) |
| `order` | PositiveIntegerField | Порядок сортировки |
| `is_active` | BooleanField | Активна ли |

**Методы:** `get_subjects_count()` — кэшированный подсчет предметов в категории.

---

### 5.3 Subject

Предметы (например, "Математика", "Английский язык").

| Поле | Тип | Описание |
|------|-----|----------|
| `category` | FK → SubjectCategory | Категория (SET_NULL) |
| `name` | CharField(100, unique) | Название |
| `description` | TextField | Описание |
| `icon` | CharField(50) | Иконка |
| `is_active` | BooleanField | Активен ли |
| `is_popular` | BooleanField | Популярный ли |

**Индексы:** `[category, is_active]`, `[is_popular, is_active]`
**Методы:** `get_teachers_count()` — кэшированный подсчет учителей по предмету.

---

### 5.4 City

Города для привязки к профилям.

| Поле | Тип | Описание |
|------|-----|----------|
| `name` | CharField(100, unique) | Название |
| `country` | CharField(100) | Страна (по умолч. "Узбекистан") |
| `is_active` | BooleanField | Активен ли |

---

### 5.5 Certificate

Сертификаты учителей.

| Поле | Тип | Описание |
|------|-----|----------|
| `name` | CharField(200) | Название |
| `issuer` | CharField(200) | Кто выдал |
| `file` | FileField | Файл сертификата |

---

### 5.6 TeacherProfile (основная модель)

Профиль учителя — самая большая модель проекта.

| Поле | Тип | Описание |
|------|-----|----------|
| `user` | OneToOne → User | Связь с пользователем |
| `bio` | TextField(1000) | Описание/биография |
| `education_level` | CharField(20) | `bachelor`, `master`, `phd`, `other` |
| `university` | CharField(200) | ВУЗ |
| `specialization` | CharField(200) | Специализация |
| `experience_years` | PositiveIntegerField | Опыт (0-50 лет) |
| `subjects` | M2M → Subject | Предметы (через TeacherSubject) |
| `city` | FK → City | Город |
| `teaching_format` | CharField(10) | `online`, `offline`, `both` |
| `telegram` | CharField(100) | Telegram-контакт |
| `whatsapp` | CharField(20) | WhatsApp |
| `teaching_languages` | CharField(100) | Языки преподавания (CSV) |
| `available_from` | TimeField | Начало рабочего дня (09:00) |
| `available_to` | TimeField | Конец рабочего дня (21:00) |
| `available_weekdays` | CharField(20) | Дни недели (CSV: "1,2,3,4,5,6,7") |
| `weekly_schedule` | JSONField | Расписание по дням |
| `rating` | DecimalField(3,2) | Средний рейтинг (0.00-5.00) |
| `total_reviews` | PositiveIntegerField | Кол-во отзывов |
| `total_students` | PositiveIntegerField | Кол-во учеников |
| `is_featured` | BooleanField | Избранный учитель |
| `is_active` | BooleanField | Активен ли профиль |
| `ranking_score` | PositiveIntegerField | Рейтинг для сортировки (0-100) |
| `certificates` | M2M → Certificate | Сертификаты |
| `moderation_status` | CharField(20) | `pending`, `approved`, `rejected` |
| `moderation_comment` | TextField | Комментарий модератора |
| `moderation_date` | DateTimeField | Дата модерации |
| `moderated_by` | FK → User | Модератор |

**Индексы (6 штук):**
- `[-is_featured, -ranking_score, -rating]`
- `[-rating, -created_at]`
- `[is_active, moderation_status]`
- `[city, is_active]`
- `[teaching_format]`
- `[experience_years]`

**Ключевые методы:**
- `update_ranking_score()` — расчет рейтинга (0-100) на основе featured, rating, reviews, полноты профиля
- `approve(moderator)` — одобрение профиля + уведомление
- `reject(moderator)` — отклонение профиля + уведомление
- `get_teaching_languages_list()` — парсинг кодов языков в названия
- `get_views_count(period)` — кэшированный подсчет просмотров
- `get_unique_viewers_count(period)` — уникальные просмотры
- `get_subjects_display()` — первые 3 предмета как строка
- `get_min_price()` — кэшированная минимальная цена
- `clear_cache()` — очистка всех связанных кэшей

---

### 5.7 TeacherSubject (through-модель)

Связь учителя и предмета с ценой.

| Поле | Тип | Описание |
|------|-----|----------|
| `teacher` | FK → TeacherProfile | Учитель |
| `subject` | FK → Subject | Предмет |
| `hourly_rate` | DecimalField(10,2) | Цена за час |
| `is_free_trial` | BooleanField | Бесплатное пробное |
| `description` | TextField | Описание |

**Ограничения:** `unique_together: [teacher, subject]`
**Индексы:** `[teacher, hourly_rate]`, `[subject]`

---

### 5.8 StudentProfile

Профиль ученика.

| Поле | Тип | Описание |
|------|-----|----------|
| `user` | OneToOne → User | Связь с пользователем |
| `education_level` | CharField(20) | `elementary`, `middle`, `high`, `university`, `adult` |
| `school_university` | CharField(200) | Учебное заведение |
| `city` | FK → City | Город |
| `interests` | M2M → Subject | Интересы |
| `desired_subjects` | M2M → Subject | Желаемые предметы |
| `bio` | TextField(500) | Краткое описание |
| `description` | TextField(1000) | Подробное описание |
| `budget_min` | DecimalField(10,2) | Минимальный бюджет |
| `budget_max` | DecimalField(10,2) | Максимальный бюджет |
| `learning_format` | CharField(10) | `online`, `offline`, `both` |
| `telegram` | CharField(100) | Telegram |
| `whatsapp` | CharField(20) | WhatsApp |
| `is_active` | BooleanField | Активен ли |
| `available_weekdays` | CharField(20) | Дни недели |

**Методы:** `get_desired_subjects_display()`, `get_budget_display()`, `get_views_count()`, `clear_cache()`

---

### 5.9 Conversation

Переписка между учителем и учеником.

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUIDField (PK) | UUID |
| `teacher` | FK → TeacherProfile | Учитель |
| `student` | FK → User | Ученик |
| `subject` | FK → Subject | Предмет обсуждения |
| `is_active` | BooleanField | Активна ли |

**Ограничения:** `unique_together: [teacher, student]`
**Методы:** `get_last_message()`, `get_unread_count(user)`

---

### 5.10 Message

Сообщения в переписке.

| Поле | Тип | Описание |
|------|-----|----------|
| `conversation` | FK → Conversation | Переписка |
| `sender` | FK → User | Отправитель |
| `content` | TextField(2000) | Текст сообщения |
| `is_read` | BooleanField | Прочитано ли |
| `read_at` | DateTimeField | Время прочтения |

**Индексы:** `[conversation, -created_at]`, `[sender, -created_at]`, `[is_read]`
**Методы:** `mark_as_read()` — отметка как прочитанного с таймстампом.
**Сигнал:** `post_save` → отправка уведомления через Telegram и WebSocket.

---

### 5.11 Review

Отзывы об учителях.

| Поле | Тип | Описание |
|------|-----|----------|
| `teacher` | FK → TeacherProfile | Учитель |
| `student` | FK → User | Ученик |
| `subject` | FK → Subject | Предмет |
| `rating` | PositiveIntegerField | Общий рейтинг (1-5) |
| `comment` | TextField(1000) | Комментарий |
| `knowledge_rating` | PositiveIntegerField | Рейтинг знаний (1-5) |
| `communication_rating` | PositiveIntegerField | Рейтинг общения (1-5) |
| `punctuality_rating` | PositiveIntegerField | Рейтинг пунктуальности (1-5) |
| `is_verified` | BooleanField | Верифицирован ли |

**Ограничения:** `unique_together: [teacher, student, subject]`
**Сигнал:** `post_save/delete` → обновление рейтинга и ranking_score учителя.

---

### 5.12 Favorite

Избранные учителя (добавляют ученики).

| Поле | Тип | Описание |
|------|-----|----------|
| `student` | FK → User | Ученик |
| `teacher` | FK → TeacherProfile | Учитель |

**Ограничения:** `unique_together: [student, teacher]`

---

### 5.13 FavoriteStudent

Избранные ученики (добавляют учителя).

| Поле | Тип | Описание |
|------|-----|----------|
| `teacher` | FK → TeacherProfile | Учитель |
| `student` | FK → StudentProfile | Ученик |

**Ограничения:** `unique_together: [teacher, student]`

---

### 5.14 TelegramUser

Связь Telegram-аккаунта с платформой.

| Поле | Тип | Описание |
|------|-----|----------|
| `user` | OneToOne → User | Связанный аккаунт (nullable) |
| `telegram_id` | BigIntegerField (unique) | Telegram ID |
| `telegram_username` | CharField(100) | @username |
| `first_name` | CharField(200) | Имя в Telegram |
| `last_name` | CharField(200) | Фамилия в Telegram |
| `language_code` | CharField(10) | Код языка |
| `notifications_enabled` | BooleanField | Включены ли уведомления |
| `started_bot` | BooleanField | Запускал ли бот |
| `last_interaction` | DateTimeField | Последнее взаимодействие |

---

### 5.15 ProfileView

Отслеживание просмотров профилей.

| Поле | Тип | Описание |
|------|-----|----------|
| `profile_type` | CharField(10) | `'teacher'` / `'student'` |
| `viewer_ip` | GenericIPAddressField | IP просматривающего |
| `viewer_user` | FK → User | Пользователь (если авторизован) |
| `teacher_profile` | FK → TeacherProfile | Просмотренный учитель |
| `student_profile` | FK → StudentProfile | Просмотренный ученик |
| `user_agent` | TextField | User-Agent браузера |

---

### 5.16 NotificationQueue

Очередь уведомлений для отправки через Telegram.

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | UUIDField (PK) | UUID |
| `recipient` | FK → User | Получатель |
| `notification_type` | CharField(20) | `new_message`, `new_review`, `profile_view`, `system`, `broadcast` |
| `title` | CharField(200) | Заголовок |
| `message` | TextField | Текст |
| `data` | JSONField | Дополнительные данные |
| `status` | CharField(20) | `pending`, `processing`, `sent`, `failed`, `cancelled` |
| `retry_count` | PositiveIntegerField | Текущая попытка |
| `max_retries` | PositiveIntegerField | Макс. попыток (5) |
| `last_error` | TextField | Последняя ошибка |
| `scheduled_at` | DateTimeField | Запланированное время |
| `sent_at` | DateTimeField | Время отправки |
| `idempotency_key` | CharField(255, unique) | SHA256-ключ дедупликации |

**Методы:**
- `can_retry()` — можно ли повторить
- `mark_as_processing()` / `mark_as_sent()` / `mark_as_failed(error)`
- `calculate_next_retry_delay()` — экспоненциальная задержка

---

### 5.17 NotificationLog

Лог попыток отправки уведомлений.

| Поле | Тип | Описание |
|------|-----|----------|
| `notification` | FK → NotificationQueue | Уведомление |
| `attempt_number` | PositiveIntegerField | Номер попытки |
| `status` | CharField(20) | `success`, `error`, `skipped` |
| `error_message` | TextField | Сообщение об ошибке |
| `telegram_message_id` | BigIntegerField | ID сообщения в Telegram |
| `processing_time_ms` | PositiveIntegerField | Время обработки (мс) |

---

### 5.18 SubjectSearchLog

Логирование поисковых запросов.

| Поле | Тип | Описание |
|------|-----|----------|
| `query` | CharField(200) | Поисковый запрос |
| `user` | FK → User | Пользователь |
| `ip_address` | GenericIPAddressField | IP |
| `found_results_count` | PositiveIntegerField | Кол-во результатов |
| `selected_subject` | FK → Subject | Выбранный предмет |

---

### 5.19 ViewCounter

Счетчик просмотров страниц по месяцам.

| Поле | Тип | Описание |
|------|-----|----------|
| `ip_address` | GenericIPAddressField | IP |
| `user_agent` | TextField | User-Agent |
| `page` | CharField(100) | Название страницы |
| `month` | DateField | Месяц |

**Ограничения:** `unique_together: [ip_address, user_agent, page, month]`
**Методы:** `add_view(request, page)`, `get_monthly_stats()`

---

### 5.20 Notification

Внутренние уведомления платформы.

| Поле | Тип | Описание |
|------|-----|----------|
| `title` | CharField(200) | Заголовок |
| `short_text` | CharField(300) | Краткий текст |
| `full_text` | TextField | Полный текст |
| `image` | ImageField | Изображение |
| `action_url` | URLField | Ссылка для действия |
| `target` | CharField(20) | `all`, `students`, `teachers`, `admins`, `specific_user` |
| `target_user` | FK → User | Конкретный пользователь |
| `is_active` | BooleanField | Активно ли |
| `priority` | IntegerField | Приоритет |
| `created_by` | FK → User | Автор |

**Ключевые методы:**
- `is_visible_for_user(user)` — проверка видимости
- `get_unread_count(user)` — подсчет непрочитанных (classmethod)
- `get_user_notifications(user)` — получение уведомлений для пользователя
- `mark_as_read(user)` — отметка как прочитанного

---

### 5.21 NotificationRead

Отметки о прочтении уведомлений.

| Поле | Тип | Описание |
|------|-----|----------|
| `user` | FK → User | Пользователь |
| `notification` | FK → Notification | Уведомление |
| `read_at` | DateTimeField | Время прочтения |

**Ограничения:** `unique_together: [user, notification]`

---

## 6. URL-маршрутизация

### Корневая маршрутизация (core/urls.py)
- `i18n/setlang/` — смена языка
- `admin/` — Django admin
- Все маршруты обернуты в `i18n_patterns()` с `prefix_default_language=False`

### Основные маршруты (teachers/urls.py) — 97 паттернов

#### Аутентификация
| URL | View | Метод | Описание |
|-----|------|-------|----------|
| `/login/` | `login_view` | GET/POST | Вход |
| `/logout/` | `logout_view` | GET | Выход |
| `/register/choose/` | `register_choose` | GET | Выбор типа регистрации |
| `/register/student/` | `register_student` | GET/POST | Регистрация ученика |

#### Регистрация учителя (визард)
| URL | View | Метод | Описание |
|-----|------|-------|----------|
| `/register/` | `TeacherRegistrationWizard` | GET/POST | 6-шаговый визард |
| `/register/complete/` | `teacher_register_complete` | GET | Страница завершения |

#### Профили
| URL | View | Метод | Описание |
|-----|------|-------|----------|
| `/profile/` | `profile_view` | GET | Свой профиль |
| `/profile/edit/` | `profile_edit` | GET | Редирект на форму |
| `/profile/edit/teacher/` | `teacher_profile_edit` | GET/POST | Редактирование учителя |
| `/profile/edit/student/` | `student_profile_edit` | GET/POST | Редактирование ученика |
| `/profile/toggle-status/` | `toggle_profile_status` | POST | Вкл/выкл профиль |

#### Учителя и ученики
| URL | View | Метод | Описание |
|-----|------|-------|----------|
| `/` | `home` | GET | Главная (список учителей) |
| `/teacher/<id>/` | `detail` | GET | Профиль учителя |
| `/students/` | `students_list` | GET | Список учеников |
| `/student/<id>/` | `student_detail` | GET | Профиль ученика |

#### Избранное
| URL | View | Метод | Описание |
|-----|------|-------|----------|
| `/favorites/teachers/` | `my_favorite_teachers` | GET | Мои избранные учителя |
| `/favorites/students/` | `my_favorite_students` | GET | Мои избранные ученики |
| `/api/favorites/toggle/<id>/` | `toggle_favorite_teacher` | POST | Добавить/убрать учителя |
| `/api/favorites/student/toggle/<id>/` | `toggle_favorite_student` | POST | Добавить/убрать ученика |

#### Сообщения (чат)
| URL | View | Метод | Описание |
|-----|------|-------|----------|
| `/messages/` | `conversations_list` | GET | Список переписок |
| `/messages/<uuid>/` | `conversation_detail` | GET | Переписка |
| `/messages/start/<user_id>/` | `start_conversation` | POST | Начать переписку |
| `/api/messages/<uuid>/send/` | `send_message_ajax` | POST | Отправить сообщение |
| `/api/messages/<uuid>/read/` | `mark_messages_read` | POST | Отметить прочитанным |
| `/messages/<uuid>/delete/` | `delete_conversation` | POST | Удалить переписку |

#### Уведомления
| URL | View | Метод | Описание |
|-----|------|-------|----------|
| `/notifications/` | `notifications_list` | GET | Список уведомлений |
| `/notifications/<id>/` | `notification_detail` | GET | Детали уведомления |
| `/notifications/<id>/mark-read/` | `mark_notification_read` | POST | Отметить прочитанным |
| `/notifications/mark-all-read/` | `mark_all_notifications_read` | POST | Прочитать все |
| `/api/notifications/dropdown/` | `notifications_dropdown` | GET | Последние 5 (JSON) |
| `/api/badge-counts/` | `badge_counts` | GET | Счетчики бейджей |

#### API — предметы
| URL | View | Метод | Описание |
|-----|------|-------|----------|
| `/api/subjects/autocomplete/` | `subjects_autocomplete` | GET | Автодополнение (?q=) |
| `/api/subjects/popular/` | `subjects_popular` | GET | Популярные предметы |
| `/api/subjects/categories/` | `subjects_categories` | GET | Категории |
| `/api/subjects/category/<id>/` | `subjects_by_category` | GET | Предметы в категории |

#### API — Telegram
| URL | View | Метод | Описание |
|-----|------|-------|----------|
| `/api/telegram/auth/` | `telegram_auth` | POST | Аутентификация через Telegram |
| `/api/telegram/link/` | `link_telegram_account` | POST | Привязка аккаунта |
| `/api/telegram/status/` | `telegram_status` | GET | Статус подключения |
| `/api/telegram/notifications/toggle/` | `toggle_notifications` | POST | Вкл/выкл уведомления |

#### Админ-дашборд
| URL | View | Метод | Описание |
|-----|------|-------|----------|
| `/admin-dashboard/` | `admin_dashboard` | GET | Главная админки |
| `/admin-dashboard/telegram/` | `telegram_management` | GET | Управление Telegram |
| `/admin-dashboard/telegram/broadcast/` | `send_broadcast_message` | POST | Рассылка |
| `/admin-dashboard/telegram/individual/` | `send_individual_message` | POST | Личное сообщение |
| `/admin-dashboard/telegram/export/` | `export_telegram_users` | GET | Экспорт CSV |
| `/admin-dashboard/messages/` | `messages_management` | GET | Управление сообщениями |

#### WebSocket маршруты
| URL | Consumer | Описание |
|-----|----------|----------|
| `ws/notifications/` | `NotificationConsumer` | Push-уведомления |
| `ws/conversation/<uuid>/` | `ChatConsumer` | Реальное время чата |

---

## 7. Views — представления

### Главная страница (`home`)
- **URL:** `/`
- **Шаблон:** `logic/home.html`
- **Авторизация:** Не требуется
- Отображает список активных, одобренных учителей с пагинацией (12/страница)
- Фильтры: предмет, город, формат, диапазон цен, рейтинг, опыт
- Полнотекстовый поиск с весами релевантности:
  - Предметы: 3x вес (точное совпадение: 100, начинается с: 90, содержит: 80)
  - Имена: 2x вес (70/60/50)
  - Биография: 1x вес (40)
- Сортировка: рекомендуемые, рейтинг, цена, опыт, новизна
- Featured-учителя отображаются первыми
- Запись просмотров в ViewCounter

### Детальная страница учителя (`detail`)
- **URL:** `/teacher/<id>/`
- **Шаблон:** `logic/teacher_detail.html`
- Загрузка профиля с related: предметы, сертификаты, отзывы
- Расчет статистики рейтингов (знания, общение, пунктуальность)
- Распределение оценок (5-звездочная разбивка)
- Запись просмотра для аналитики
- Предложение похожих учителей по предметам

### Регистрация учителя (`TeacherRegistrationWizard`)
- **URL:** `/register/`
- 6-шаговый `SessionWizardView`:
  1. Базовый профиль (фото, имя, пол, языки, телефон)
  2. Безопасность аккаунта (логин, пароль, email)
  3. Образование (уровень, ВУЗ, специализация, опыт)
  4. Доступность (Telegram, город, формат, расписание по дням)
  5. Предметы и цены (до 4 предметов с ценами и пробным уроком)
  6. Сертификаты (опционально)
- На завершение: создание User → TeacherProfile → TeacherSubject → Certificate
- Автологин и редирект на страницу завершения

### Система сообщений
- `conversations_list` — список переписок с оптимизированными запросами (Prefetch)
- `conversation_detail` — история сообщений + WebSocket для реального времени
- `start_conversation` — создание или получение существующей переписки
- `send_message_ajax` — AJAX-отправка сообщения (JSON-ответ)
- `mark_messages_read` — отметка сообщений как прочитанных
- `delete_conversation` — мягкое удаление (is_active=False)

### Админ-дашборд (`admin_dashboard`)
- **URL:** `/admin-dashboard/`
- **Доступ:** Только staff
- Метрики: пользователи, сообщения (сегодня/неделя), просмотры, отзывы
- Telegram-статистика
- Списки: ожидающие модерации, недавние сообщения, новые регистрации
- Топ предметов

### Автодополнение предметов (`subjects_autocomplete`)
- Ранжирование по релевантности (exact: 4, starts_with: 3, contains: 2, in_description: 1)
- Логирование запросов в SubjectSearchLog
- Лимит: 30 результатов

---

## 8. Формы

### Формы регистрации учителя (6 шагов)
| Шаг | Форма | Поля |
|-----|-------|------|
| 1 | `Step1BasicProfileForm` | avatar, first_name, last_name, gender, teaching_languages, phone |
| 2 | `Step2AccountSecurityForm` | username, email, password1, password2 |
| 3 | `Step3EducationExperienceForm` | bio (мин 50 символов), education_level, university, specialization, experience_years |
| 4 | `Step4AvailabilityFormatForm` | telegram, city, teaching_format, расписание по дням недели |
| 5 | `Step5SubjectsPricingForm` | До 4 предметов: subject, hourly_rate, is_free_trial, description |
| 6 | `Step6CertificatesForm` | file (PDF/изображения), name, issuer |

### Формы редактирования
| Форма | Назначение |
|-------|-----------|
| `UserProfileEditForm` | Имя, email, телефон, возраст, аватар |
| `TeacherProfileEditForm` | Bio, образование, опыт, языки, расписание, контакты |
| `StudentProfileEditForm` | Интересы, предметы, бюджет, формат обучения |
| `MessageForm` | Текст сообщения (1-2000 символов) |
| `LoginForm` | Расширяет AuthenticationForm |
| `StudentRegistrationForm` | Одностраничная регистрация ученика |

---

## 9. WebSocket consumers

### NotificationConsumer
- **Маршрут:** `ws/notifications/`
- **Группа:** `notifications_{user_id}`
- **Назначение:** Push-уведомления и обновление бейджей в реальном времени

| Событие (клиент → сервер) | Описание |
|---------------------------|----------|
| `ping` | Keep-alive |
| `get_badges` | Запрос текущих счетчиков |

| Событие (сервер → клиент) | Описание |
|---------------------------|----------|
| `badge_update` | `{unread_messages, unread_notifications}` |
| `new_message` | Превью нового сообщения |
| `new_notification` | Новое уведомление |

### ChatConsumer
- **Маршрут:** `ws/conversation/<conversation_id>/`
- **Группа:** `chat_{conversation_id}`
- **Назначение:** Реальное время чата

| Функция | Описание |
|---------|----------|
| Авторизация | Только участники переписки |
| Rate limiting | 5 сообщений за 60 секунд |
| Валидация | 1-5000 символов |
| История | Последние 50 сообщений при подключении |
| Статус прочтения | Отслеживание read_at |

| Событие | Описание |
|---------|----------|
| `chat_message` | Отправка/получение сообщений |
| `mark_as_read` | Отметка как прочитанного |
| `message_history` | Начальная загрузка истории |
| `error` | Ошибки |

### Вспомогательная функция: `notify_user()`
- Отправка real-time события из синхронного контекста (views, signals)
- Использует `async_to_sync` обертку
- Типы событий: `new_message`, `new_notification`, `badge_update`

---

## 10. Telegram-бот и уведомления

### Бот (telegram_bot/bot.py)

| Команда | Описание |
|---------|----------|
| `/start` | Регистрация/обновление TelegramUser, кнопка WebApp |
| `/help` | Справка по командам |
| `/profile` | Профиль или ссылка на регистрацию |
| `/notifications` | Вкл/выкл уведомлений |

- Polling-режим работы
- Inline-кнопки (О нас, Настройки уведомлений)
- Автоматическая запись `started_bot=True`

### Простой сервис уведомлений (notifications.py)
- `TelegramNotificationService` — async/sync отправка
- `notify_new_message()` — уведомление о новом сообщении
- `send_broadcast()` — массовая рассылка
- Поиск получателя: сначала по привязанному аккаунту, затем fuzzy-поиск

### Продвинутый сервис (notification_service.py)

**Rate Limiting:**
- Глобальный: 25 сообщений/сек
- На пользователя: 15 сообщений/мин
- Алгоритм token bucket

**Idempotency:**
- SHA256-хэш из recipient + notification_type + data
- Предотвращение дублей в пределах одного контекста

**Retry Logic (экспоненциальная задержка):**
- Максимум 5 попыток
- Задержки: 5с → 10с → 20с → 40с → 80с
- Специальная обработка: `RetryAfter`, `TimedOut`, `NetworkError`, "blocked"

### AdminTelegramService (admin_telegram_service.py)
- `send_message_simple()` — отправка через urllib (надежнее для админки)
- `send_to_selected_users()` — пакетная рассылка (50 юзеров/батч, 2с задержка)
- `send_to_all_started_users()` — рассылка всем с фильтром по типу
- `get_ready_users_count()` — подсчет готовых получателей

### Telegram Views (telegram_views.py)

| Endpoint | Метод | Описание |
|----------|-------|----------|
| `/api/telegram/auth/` | POST | HMAC-SHA256 верификация из Telegram WebApp |
| `/api/telegram/link/` | POST | Привязка аккаунта к Telegram |
| `/api/telegram/status/` | GET | Статус подключения |
| `/api/telegram/notifications/toggle/` | POST | Вкл/выкл уведомления |

---

## 11. Фоновые задачи (Celery)

| Задача | Интервал | Описание |
|--------|----------|----------|
| `process_notification_queue` | 10 сек | Обработка очереди (батч по 10) |
| `retry_failed_notifications` | 1 час | Повтор неудачных (`select_for_update`) |
| `cleanup_old_notification_logs` | Ежедневно 03:00 | Удаление логов >30 дней |
| `cleanup_old_notifications` | Ежедневно 03:30 | Удаление sent/cancelled >90 дней |
| `cancel_stuck_notifications` | 15 мин | Отмена зависших >30 мин в processing |
| `health_check_notifications` | 5 мин | Мониторинг (pending, processing, failed, sent_last_hour) |

---

## 12. Сигналы Django

### Файл: teachers/signals.py

| Сигнал | Модель | Действия |
|--------|--------|----------|
| `send_message_notification` | Message (post_save) | WebSocket push + Telegram очередь + инвалидация кэша |
| `clear_subjects_cache` | Subject | Очистка `all_subjects` и кэша категорий |
| `clear_category_cache` | SubjectCategory | Очистка кэша категорий |
| `clear_cities_cache` | City | Очистка `all_cities` |
| `clear_price_range_cache` | TeacherSubject | Очистка `price_range` и цен учителя |
| `clear_budget_range_cache` | StudentProfile | Очистка `budget_range` |
| `clear_teacher_cache` | TeacherProfile | Вызов `clear_cache()` |
| `clear_teacher_reviews_cache` | Review | Очистка кэша отзывов + пересчет ranking_score |
| `update_view_stats_cache` | ProfileView | Логирование просмотров |
| `push_notification_realtime` | Notification (post_save) | WebSocket push к целевым пользователям + инвалидация кэша |

---

## 13. Кэширование

### Стратегия

| TTL | Применение |
|-----|-----------|
| 30 сек | Бейджи (непрочитанные сообщения, уведомления) |
| 60 сек (SHORT) | Часто меняющиеся данные |
| 300 сек (DEFAULT) | Основной кэш (предметы, города) |
| 3600 сек (LONG) | Редко меняющиеся данные |

### Ключи кэша

| Ключ | Описание |
|------|----------|
| `all_subjects` | Список активных предметов |
| `all_cities` | Список активных городов |
| `price_range` | Мин/макс цены учителей |
| `budget_range` | Мин/макс бюджеты учеников |
| `category_subjects_count_{id}` | Кол-во предметов в категории |
| `subject_teachers_count_{id}` | Кол-во учителей по предмету |
| `teacher_min_price_{id}` | Минимальная цена учителя |
| `teacher_views_{id}_{period}` | Просмотры профиля учителя |
| `teacher_unique_views_{id}_{period}` | Уникальные просмотры |
| `unread_messages_{user_id}` | Непрочитанные сообщения |
| `unread_notifications_{user_id}` | Непрочитанные уведомления |
| `conversations_count_{user_id}` | Кол-во активных переписок |

### Инвалидация
Автоматическая через Django-сигналы при изменении данных. Ручная — через `invalidate_message_cache()` и `invalidate_notification_cache()` в context_processors.

---

## 14. Фронтенд и шаблоны

### Иерархия шаблонов (43 файла)

**Базовый шаблон** (`base.html`):
- Фиксированный navbar с поиском
- Обработка состояния аутентификации
- Переключатель языков
- Бейджи уведомлений и сообщений (Fetch API)
- Mobile-responsive бургер-меню
- CSS-переменные для темизации

**Основные страницы** (`logic/`):
- `home.html` — главная с фильтрами и поиском
- `teacher_detail.html` — профиль учителя
- `student_detail.html` — профиль ученика
- `students_list.html` — список учеников
- `conversations_list.html` — список переписок
- `conversation_detail.html` — чат
- `favorites_teachers.html` / `favorites_students.html` — избранное

**Регистрация** (`registration/`):
- `base_wizard.html` — контейнер визарда с прогресс-баром
- `step1-6` — шаги регистрации
- `complete.html` — завершение

**Админ** (`admin/`):
- `admin_dashboard.html` — дашборд
- `telegram_management.html` — управление Telegram
- `send_broadcast.html` — рассылка

**Уведомления** (`notifications/`):
- `list.html` — список
- `detail.html` — детали

### Дизайн-система (CSS Variables)

```css
--primary: #0A2540;        /* Темно-синий */
--accent: #3B82F6;         /* Яркий синий */
--success: #10B981;        /* Зеленый */
--warning: #F59E0B;        /* Янтарный */
--danger: #EF4444;         /* Красный */
```

### JavaScript (registration.js)
- Превью фото с валидацией
- Индикатор надежности пароля
- Счетчик символов для bio
- Drag-and-drop загрузка файлов
- Автоформатирование номера телефона
- Клиентская валидация форм

### Адаптивность
- Desktop: 1024px+
- Tablet: 768px-1024px
- Mobile: 480px-768px
- Small Mobile: <480px
- Touch-оптимизация (отключение hover)
- `prefers-reduced-motion` поддержка

---

## 15. Админ-панель

### Кастомизированные модели

| Модель | Особенности |
|--------|------------|
| `UserAdmin` | Фильтры по типу, верификации; доп. поля в list_display |
| `SubjectCategoryAdmin` | Цветные иконки, управление порядком |
| `SubjectAdmin` | Бейджи категорий, флаг популярности |
| `SubjectSearchLogAdmin` | Read-only аналитика, популярные запросы |
| `TeacherProfileAdmin` | Inline TeacherSubject, действия: approve/reject/recalculate |
| `StudentProfileAdmin` | Фильтры по городу, формату; editable is_active |
| `ProfileViewAdmin` | Аналитика просмотров, date hierarchy |
| `CertificateAdmin` | Поиск по имени/издателю |
| `TelegramUserAdmin` | Управление Telegram-пользователями |

### Действия модерации (TeacherProfileAdmin)
- **approve_teachers()** — массовое одобрение + отправка уведомлений
- **reject_teachers()** — массовое отклонение + отправка уведомлений
- **recalculate_rankings()** — пересчет ranking_score

---

## 16. Безопасность

### Production-настройки (DEBUG=False)

| Настройка | Значение |
|-----------|----------|
| `SECURE_SSL_REDIRECT` | True |
| `SESSION_COOKIE_SECURE` | True |
| `CSRF_COOKIE_SECURE` | True |
| `SECURE_BROWSER_XSS_FILTER` | True |
| `SECURE_CONTENT_TYPE_NOSNIFF` | True |
| `X_FRAME_OPTIONS` | DENY |
| `SECURE_HSTS_SECONDS` | 63072000 (2 года) |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | True |
| `SECURE_HSTS_PRELOAD` | True |

### Валидация паролей
- Минимальная длина: 10 символов
- Проверка схожести с атрибутами пользователя
- Проверка на распространенные пароли

### Авторизация
- `@login_required` — для защищенных views
- `@staff_member_required` — для админ-дашборда
- `@require_POST` — для API-эндпоинтов
- Проверка доступа к перепискам (только участники)
- HMAC-SHA256 верификация Telegram WebApp
- CSRF-защита на всех формах

### CSRF Trusted Origins
- `https://ustozhubedu.uz`
- `https://www.ustozhubedu.uz`

---

## 17. Бизнес-логика и пользовательские сценарии

### Сценарий 1: Регистрация учителя
```
1. Пользователь открывает /register/
2. Проходит 6 шагов визарда с валидацией на каждом
3. На завершение создаются:
   - User (user_type='teacher', is_verified=False)
   - TeacherProfile (moderation_status='pending', is_active=False)
   - TeacherSubject записи (до 4 предметов)
   - Certificate записи (опционально)
4. Автоматический вход
5. Редирект на страницу "Ожидание модерации"
```

### Сценарий 2: Одобрение учителя
```
1. Админ видит профиль в Django admin
2. Выбирает действие "Одобрить"
3. TeacherProfile.approve() выполняется:
   - moderation_status → 'approved'
   - is_active → True
   - moderated_by, moderation_date заполняются
   - Создается Notification для учителя
   - Отправляется Telegram-уведомление
4. Профиль появляется в поиске
```

### Сценарий 3: Отправка сообщения
```
1. Пользователь A отправляет сообщение через WebSocket
2. ChatConsumer валидирует и сохраняет в БД
3. Сигнал Message.post_save:
   - notify_user(B, 'new_message', ...) → WebSocket push
   - queue_new_message_notification(B) → NotificationQueue
   - invalidate_message_cache(B)
4. ChatConsumer broadcast → оба пользователя получают обновление
5. Через ~10 сек Celery task отправляет Telegram-уведомление
```

### Сценарий 4: Поиск учителя
```
1. Пользователь вводит запрос на главной
2. Расчет релевантности по весам:
   - Предметы (3x): exact(100), starts_with(90), contains(80)
   - Имена (2x): exact(70), starts_with(60), contains(50)
   - Bio (1x): contains(40)
3. Фильтрация + сортировка по релевантности
4. Запись в SubjectSearchLog для аналитики
```

### Сценарий 5: Восстановление неудачного уведомления
```
1. Уведомление застряло в 'processing' > 30 мин
2. cancel_stuck_notifications (каждые 15 мин):
   - Находит зависшее уведомление
   - retry_count < 5 → status='pending' (повтор)
   - retry_count >= 5 → status='failed' (отказ)
3. process_notification_queue подхватывает pending → отправляет
```

---

## 18. Деплой и инфраструктура

### Необходимые сервисы
1. **Redis:** `redis-server` на `localhost:6379`
2. **Daphne ASGI:** `daphne -b 0.0.0.0 -p 8000 core.asgi:application`
3. **Celery Worker:** `celery -A core worker -l info`
4. **Celery Beat:** `celery -A core beat -l info`
5. **Telegram Bot:** `python telegram_bot/bot.py`

### Переменные окружения
```
SECRET_KEY=<django-secret>
DEBUG=False
TELEGRAM_BOT_TOKEN=<от @BotFather>
SITE_URL=https://ustozhubedu.uz
```

### Allowed Hosts
- `ustozhubedu.uz`
- `www.ustozhubedu.uz`
- `localhost`
- `127.0.0.1`

### Оптимизации производительности
- `select_related()` для FK
- `prefetch_related()` для M2M
- `only()` и `values()` для частичных выборок
- `aggregate()` для вычислений
- 6+ индексов на TeacherProfile
- Кэширование с автоматической инвалидацией
- Пагинация (12 учителей/страница, 15 уведомлений/страница)
- WhiteNoise сжатие и версионирование статики

---

## 19. Статистика проекта

| Метрика | Значение |
|---------|----------|
| Моделей | 21 |
| Views | 40+ |
| URL-маршрутов | ~97 |
| HTML-шаблонов | 43 |
| Форм | 40+ |
| WebSocket consumers | 2 |
| Celery-задач | 6 |
| Django-сигналов | 10 |
| Языков интерфейса | 3 |
| Management-команд | 4 |
| Миграций | 19 |
| Python-зависимостей | 40+ |
| Строк кода (models+views+forms) | ~4850 |
| Размер БД (dev) | ~696 KB |

---

*Документация сгенерирована для проекта UstozHub (ustozhubedu.uz)*
