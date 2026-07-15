# Системная архитектура UstozHub — обзор и диаграммы

> **Что это за документ.** Системный (инфраструктурный) взгляд на проект:
> топология процессов, структура кода, доменная модель, потоки данных, деплой.
> Дополняет два соседних документа, не дублируя их:
> - `docs/ARCHITECTURE.md` — **нормативные паттерны** проектирования и рефакторинг-роадмап;
> - `PROJECT_DOCUMENTATION.md` — **функциональное** описание фич;
> - `deploy/DEPLOY.md` — процедура деплоя.

**UstozHub** (ustozhubedu.uz) — образовательный маркетплейс «ученик ↔ учитель»
для Узбекистана: поиск учителей, бронирование слотов, подписки на курсы,
онлайн-уроки (видео + чат + доска), внутренний кошелёк с escrow-логикой.
Трёхъязычный: `ru` (источник) / `uz` / `en`.

---

## 1. Технологический стек

| Слой | Технология |
|------|-----------|
| Backend | Django 5.2 (Python), моно-репозиторий |
| HTTP-сервер | Gunicorn (WSGI, unix-socket) — основной трафик |
| ASGI-сервер | Daphne (WebSocket) — Django Channels 4 |
| Realtime | Django Channels + channels-redis (Redis pub/sub) |
| Фоновые задачи | Celery 5.4 (worker + beat), брокер Redis |
| Кэш / сессии | Redis (`cached_db`-сессии); LocMem в dev |
| БД | PostgreSQL (prod) / SQLite (dev) |
| Frontend | Django-шаблоны (SSR) + WhiteNoise для статики |
| Аутентификация | django-allauth (Google OAuth2) + кастомный `User` |
| Платежи | Multicard (эквайринг, пополнение кошелька) |
| Мессенджер | python-telegram-bot (бот + канал @UstozHubUz) |
| Видео-уроки | self-hosted Jitsi (meet.ustozhubedu.uz) + Excalidraw |
| Мониторинг | Sentry (опц.), `/healthz` (DB + Redis) |
| Reverse proxy | Nginx (TLS, статика/медиа, роутинг ws/http) |

---

## 2. Runtime-топология процессов

Каждый процесс — отдельный **systemd-юнит** (`deploy/*.service`): `gunicorn`,
`daphne`, `celery`, `celery-beat`, `telegram-bot`, `telegram-poll`.
Все читают одну кодовую базу и общий Redis.

```
                          ┌──────────────────────────────────────┐
                          │              NGINX (443 / TLS)         │
                          │      server_name: ustozhubedu.uz       │
                          └───┬───────────────┬──────────────┬─────┘
              /static/,/media/│          /ws/ │          /   │ (http)
              (отдаёт сам)    │   (WebSocket) │              │
                              ▼               ▼              ▼
                          файлы        ┌───────────┐  ┌──────────────┐
                        на диске        │  DAPHNE   │  │  GUNICORN     │
                                        │  (ASGI)   │  │  (WSGI,       │
                                        │ Channels  │  │  unix-socket) │
                                        └─────┬─────┘  └──────┬────────┘
                                              │ core.asgi     │ core.wsgi
                                              ▼               ▼
                                        ┌──────────────────────────────┐
   ┌────────────┐                       │      Django-приложения         │
   │  Multicard │───callback──────────▶ │      teachers · billing        │
   │  эквайринг │                       └───┬──────────┬────────┬───────┘
   └────────────┘                           │          │        │
                                            ▼          ▼        ▼
   ┌────────────┐   бот (poll/API)     ┌─────────┐ ┌────────┐ ┌─────────┐
   │  Telegram  │◀────────────────────│  Redis   │ │ Postgre│ │ Media   │
   │ Bot+Channel│                      │ 0 channels│ │  SQL   │ │ (FS)    │
   └────────────┘                      │ 1 cache   │ │        │ │         │
                                        │ 2 broker  │ └────────┘ └─────────┘
   ┌────────────┐   iframe (P2P)        │ 3 results │
   │ Jitsi meet │◀─── видео/доска ──────└────┬─────┘
   └────────────┘                            │
                                        ┌─────┴───────┐
                                        │  CELERY      │
                                        │ worker + beat│ (systemd)
                                        └──────────────┘
```

**Разделение Redis по номерам БД:**
`0` — Channels (pub/sub WebSocket) · `1` — кэш и сессии ·
`2` — брокер Celery · `3` — результаты Celery.

---

## 3. Логическая структура кода

```
core/                    # проектный слой (конфиг, точки входа)
 ├─ settings.py          # единый env-driven конфиг: i18n, security, Redis, Celery
 ├─ urls.py              # корневой роутинг: i18n_patterns + webhook/sitemap ВНЕ i18n
 ├─ asgi.py              # ProtocolTypeRouter: http→Django, ws→Channels
 ├─ wsgi.py              # точка входа Gunicorn
 └─ celery.py            # Celery app + beat_schedule (все периодические задачи)

teachers/                # ЯДРО домена: люди, профили, поиск, брони, чат, realtime
 ├─ models.py            # ~40 моделей (User, TeacherProfile, StudentProfile,
 │                       #   TimeSlot, Booking, Conversation, Message, Review,
 │                       #   Notification*, StudentInterest, TelegramUser …)
 ├─ consumers.py         # WebSocket: Notification / Chat / LessonRoom
 ├─ routing.py           # ws-URL: /ws/notifications, /ws/lesson/<id>, /ws/conversation/<id>
 ├─ booking_views.py     # жизненный цикл брони  (рефакторинг-таргет → BookingService)
 ├─ registration_wizard.py # многошаговая регистрация учителя (formtools)
 ├─ search.py            # поиск и фильтрация учителей
 ├─ leads.py             # StudentInterest — агрегат лидов
 ├─ tasks.py             # Celery: неявки, напоминания, авто-completion, слоты
 ├─ signals.py           # доменные события → уведомления (Observer)
 ├─ middleware.py        # OnboardingMiddleware, CSPReportOnlyMiddleware
 └─ context_processors.py# бейджи/счётчики в каждом шаблоне

billing/                 # ДЕНЬГИ: кошелёк, escrow, подписки, выплаты, споры
 ├─ models.py            # Wallet, Transaction, Tariff, Subscription,
 │                       #   WithdrawalRequest, Homework*, LessonDispute, MulticardInvoice
 ├─ services.py          # ВСЯ бизнес-логика денег (WalletService, SubscriptionService,
 │                       #   TrialService, WithdrawalService, DisputeService)
 │                       #   — атомарно и идемпотентно  ⭐ Service Layer
 ├─ multicard.py         # интеграция эквайринга (Bearer-токен, md5-подпись)  Gateway
 ├─ platform_account.py  # системный счёт платформы (комиссия)
 ├─ tasks.py             # release_pending_payouts, reconcile_*, settle_*  (Sweep)
 └─ admin_views.py       # админ-хаб биллинга (отчёты, выплаты, споры)

telegram_bot/            # мост в Telegram
 ├─ bot.py               # /start, deep-link привязка, WebApp
 ├─ notification_service.py + tasks.py  # очередь → отправка (process_notification_queue)
 ├─ channel_publisher.py # авто-пост одобренного учителя в канал
 └─ account_link.py      # привязка TG-аккаунта к User через токен
```

---

## 4. Доменная модель

### 4.1. Люди, обучение, взаимодействие (`teachers`)

```
        User (AbstractUser; роли: student / teacher / staff)
       /                                              \
 StudentProfile  1─1                              1─1  TeacherProfile
   │  interests (M2M Subject)                       │  subjects (M2M через TeacherSubject)
   │  desired_subjects (M2M)                        │  city, certificates (M2M)
   │  city                                          │  moderated_by (модерация)
   │                                                │
   │                                        ┌───────┴────────┐
   │                                        ▼                ▼
   │                                   TimeSlot  1─N      Tariff (цена курса:
   │                                    (окно 4 нед,        уроков/нед, длит-ть,
   │                                     авто-replenish)     месяцев)
   │                                        │                 │
   ▼                                        ▼ slot 1─1         ▼
 Favorite / StudentInterest             Booking  ──────►  Subscription
 (лиды: view / favorite / trial)        UUID, status-машина  (status-машина, escrow)
   │                                        │                 │
   ▼                                        ▼                 ▼
 Conversation 1─N Message              Review            Homework 1─1 HomeworkSubmission
 (чат ученик ↔ учитель)               (1 бронь = 1 отзыв)  (ДЗ + файлы + ответ ученика)

 Notification / NotificationQueue / NotificationLog
   → единый pipeline доставки (in-app + Telegram + email)
 TelegramUser 1─1 User  (привязка через deep-link токен)
```

### 4.2. Деньги (`billing`)

```
 Wallet (1─1 User)  1─N  Transaction ──┬─ related_booking (FK)
   balance (денормализ.,                └─ related_subscription (FK)
   источник правды = SUM(tx))          type:   topup / hold / payout / refund / commission
                                        status: pending / completed / …
                                        idempotency_key (UNIQUE) + balance_after (снимок)

 WithdrawalRequest   — вывод средств учителем (PENDING → APPROVED → …)
 MulticardInvoice    — пополнение кошелька через эквайринг
 LessonDispute 1─1 Booking — спор по проведению урока
 PlatformAccount     — системный счёт платформы (комиссия площадки)
```

### 4.3. State-машина `Booking`

```
                       ┌──────────── expired (не подтверждено вовремя)
                       │
   pending ───────────┼──► confirmed ──► completed          (обе стороны были → payout)
   (hold денег)       │        │     ├──► no_show_student    (ученик не пришёл → правила 50%)
                       │        │     ├──► no_show_teacher    (учитель не пришёл → refund)
                       │        │     └──► not_held           (никто → refund)
                       │        └──► cancelled_by_teacher / rescheduled
                       └──► cancelled_by_student

 Инвариант: slot = OneToOne → один слот = одна активная бронь
 (partial UNIQUE constraint one_active_booking_per_slot — защита от гонки).
```

---

## 5. Ключевые потоки данных

### 5.1. Бронирование и escrow-цикл урока (сердце системы)

```
1. Ученик бронирует TimeSlot → Booking(status=pending); OneToOne(slot) защищает от гонки.
2. Окно подтверждения учителя (expires_at). Celery release_expired_holds (каждую минуту)
   снимает протухшие holds.
3. Учитель подтверждает → confirmed. Деньги ученика ЗАМОРАЖИВАЮТСЯ (hold-транзакция).
4. Урок идёт в LessonRoom: свой WebSocket (presence, чат, файлы) + Jitsi iframe (видео/доска).
5. Celery mark_completed_lessons (каждые 5 мин) закрывает прошедшие уроки:
      обе стороны были → completed        → payout учителю (release_pending_payouts)
      ученик не пришёл  → no_show_student  → правила прощения / удержание 50%
      учитель не пришёл → no_show_teacher  → refund ученику
      никто             → not_held         → refund
6. Спор → LessonDispute → DisputeService → ручное разрешение в админ-хабе биллинга.

Вся денежная логика — в billing/services.py: транзакционно и идемпотентно
(idempotency_key + select_for_update). Ночные reconcile_* сверяют балансы кошельков
и escrow-подписок с append-only леджером транзакций.
```

### 5.2. Realtime-слой (свой WebSocket поверх Channels)

```
Daphne → core.asgi → AllowedHostsOriginValidator (анти-CSWSH) → AuthMiddlewareStack → URLRouter:

  /ws/notifications/         → NotificationConsumer  (per-user push)
  /ws/lesson/<booking_id>/   → LessonRoomConsumer     (комната урока: presence + чат + файлы)
  /ws/conversation/<id>/     → ChatConsumer            (переписка ученик ↔ учитель)

Тяжёлое видео/доска НЕ проходят через Django — это P2P Jitsi в iframe.
```

### 5.3. Единый pipeline уведомлений

```
доменное событие (signals.py)
     │
     ▼
 Notification ──мост──► NotificationQueue ──► Celery process_notification_queue
                                                   │
                        ┌──────────────────────────┼──────────────────────────┐
                        ▼                           ▼                           ▼
                  in-app (WebSocket           Telegram (бот,               email
                  NotificationConsumer)       deep-link привязка)     (send_notification_email)
                        │                           │                           │
                        └───────────► NotificationLog (аудит) ◄─────────────────┘
                              retry_failed_notifications · health_check

 Правило: прямая отправка в Telegram запрещена — только через очередь (иначе дубли).
```

### 5.4. Пополнение кошелька через Multicard

```
wallet_topup_multicard → создаёт MulticardInvoice → редирект на эквайринг Multicard
   → пользователь платит картой
   → webhook POST /payments/multicard/callback/ (ВНЕ i18n, проверка md5-подписи)
   → зачисление в Wallet (Transaction type=topup, idempotency multicard:<id>)
   → ночной reconcile_multicard_invoices добивает «зависшие» инвойсы.
```

---

## 6. Периодические задачи (Celery Beat)

```
Частые — реалтайм-корректность:
  release_expired_holds ....... 60s   снять протухшие брони (hold)
  send_lesson_reminders ....... 60s   напоминания об уроке
  mark_completed_lessons ..... 300s   авто-закрытие уроков + денежные расчёты
  release_pending_payouts .... 300s   выплаты учителям из escrow
  process/health notifications 300s   доставка и health-check уведомлений

Средние — согласованность денег:
  expire_unpaid_approvals .... 900s        reconcile_orphaned_refunds .. 1800s
  reconcile_multicard ........ 1800s       settle_expired_subscriptions .. 1h

Ночные — сверки и уборка:
  reconcile_wallet_balances .. 04:00       reconcile_subscription_escrow . 04:30
  replenish_teacher_slots .... 01:00       cleanup_* (drafts/notifs/logs) 02:30–03:30
```

---

## 7. Сквозные аспекты (cross-cutting concerns)

- **i18n.** `LocaleMiddleware` + `i18n_patterns` (ru без префикса, uz/en с префиксом).
  Webhook Multicard, `sitemap.xml`, `robots.txt`, allauth-callback вынесены **вне** i18n.
  `.mo`-файлы в git; `ru` — язык-источник; `makemessages` всегда с `--ignore=venv`.
- **Безопасность.** `AllowedHostsOriginValidator` на WebSocket (анти-CSWSH);
  CSP в report-only; HSTS (2 года); `SECURE_PROXY_SSL_HEADER` (Django за Nginx);
  приватные сертификаты `/media/certificates/` под отдельным `location`;
  ratelimit по `X-Real-IP` (Gunicorn слушает unix-socket → `REMOTE_ADDR` пуст).
- **Онбординг.** `OnboardingMiddleware` форсит заполнение профиля пользователям без него.
- **Модерация.** Учителя проходят модерацию (`moderated_by`); после одобрения —
  авто-пост в канал @UstozHubUz (`TeacherChannelPost`, OneToOne = «ровно один раз»).
- **Лиды / аналитика.** `StudentInterest` агрегирует сигналы (view / favorite / trial)
  для «тёплых» контактов учителю.
- **Сессии.** `cached_db` — Redis-кэш с фолбэком в БД (убирает SELECT/UPDATE
  `django_session` на каждый авторизованный запрос, сохраняя durability).
- **Анти-обход контактов.** `contact_filter` маскирует телефоны/ссылки в чате и
  уведомлениях (чтобы сделки не уходили мимо платформы).

---

## 8. Инфраструктура и деплой

```
Прод-хост: 164.92.185.36 (ustozhubedu.uz)
Jitsi:     159.89.29.182 (meet.ustozhubedu.uz, docker /opt/jitsi)

systemd-юниты (deploy/*.service):
  gunicorn.service + gunicorn.socket   — WSGI (unix-socket)
  daphne.service                       — ASGI/WebSocket
  celery.service                       — worker
  celery-beat.service                  — планировщик периодических задач
  telegram-bot.service                 — бот (WebApp, deep-link)
  telegram-poll.service                — polling обновлений
  nginx.conf                           — TLS, статика/медиа, роутинг /ws/ ↔ /

Деплой: collectstatic ОБЯЗАТЕЛЕН; рестарт gunicorn/daphne/celery для подхвата
переводов (.mo) и кода. Prod — PostgreSQL + Redis (dev-venv в репозитории битый).
```

---

## 9. Архитектурные принципы (резюме)

1. **Финансовое ядро изолировано.** Вся логика денег — только в `billing/services.py`
   (Service Layer), атомарно и идемпотентно, с append-only леджером `Transaction` и
   набором `reconcile_*`-задач против рассогласований.
2. **Разделение realtime.** Лёгкий realtime (чат, presence, push) — свой WebSocket-слой
   на Channels; тяжёлое видео вынесено в отдельный Jitsi. Django не проксирует медиапотоки.
3. **Единый путь уведомлений.** Любое событие идёт через очередь `NotificationQueue`,
   а не напрямую в канал — это гарантирует отсутствие дублей и аудит через `NotificationLog`.
4. **Инварианты на уровне БД.** Гонки убираются `OneToOne`/`UNIQUE`/`CheckConstraint`
   (слот↔бронь, бронь↔отзыв, учитель↔пост, баланс ≥ 0), а не только логикой приложения.
5. **Env-driven конфиг.** Один `settings.py` разворачивается в dev (SQLite/LocMem)
   и prod (PostgreSQL/Redis) через переменные окружения.

> ⚠️ Технический долг (подробно — в `docs/ARCHITECTURE.md`, §3): приложение `teachers`
> ещё не переведено на Service Layer — lifecycle броней размазан между моделью, view и
> сервисом. Целевой рефакторинг — извлечение `BookingService` по стратегии Strangler Fig.
```
