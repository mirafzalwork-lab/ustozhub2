# Архитектура UstozHub — паттерны проектирования и правила

> Назначение документа: зафиксировать, **какие паттерны проектирования уже
> приняты в проекте**, где их не хватает, и **по каким правилам добавлять новый
> код**, чтобы архитектура не деградировала. Документ описательно-нормативный:
> код он не меняет. Решения о рефакторинге — раздел «Roadmap», строго
> инкрементально и за тестами.

Стек: Django 5.2 (монолит) · ASGI (Daphne + Channels) + WSGI (Gunicorn) ·
Celery + Redis · PostgreSQL (prod) · Multicard (платежи) · Jitsi (видео) ·
Telegram (уведомления).

---

## 1. Слои и зоны ответственности (целевая модель)

Проект — **Django-монолит со слоем сервисов**. Это сознательный выбор: не
Clean/Hexagonal Architecture (оверинжиниринг для монолита), а прагматичный
**Service Layer поверх Active Record (Django ORM)**.

```
HTTP / WebSocket / Telegram / Celery   ← точки входа (тонкие)
        │
        ▼
   Service Layer  (billing/services.py, teachers/services.py*)
        │            бизнес-логика, транзакции, деньги, переходы статусов
        ▼
   Models (Active Record)  + QuerySet/Manager (Repository-роль)
        │            данные, инварианты БД (constraints), простые свойства
        ▼
   Gateways/Adapters  (multicard.py, JitsiGateway*, notification_service.py)
        │            внешние системы
        ▼
   PostgreSQL · Redis · внешние API
```
`*` — целевое состояние (см. Roadmap).

### Правило размещения логики (главное)

| Что | Где должно жить | Где НЕ должно |
|---|---|---|
| Денежная операция, переход статуса, многошаговая бизнес-логика | **Service Layer** (`*/services.py`) | не во view, не в шаблоне |
| Валидация формата ввода | **Form / Serializer** | не в сервисе |
| Инвариант данных (баланс ≥ 0, уникальность брони слота) | **CheckConstraint / UniqueConstraint** в модели | не только в Python |
| Простое производное значение (`is_jitsi_meeting`, `progress_percent`) | свойство **модели** | не во view |
| Запрос с фильтрами, переиспользуемый ≥2 раз | метод **QuerySet/Manager** | не копипастой по вьюхам |
| Вызов внешнего API | **Gateway/Adapter** | не в сервисе напрямую |
| Оркестрация HTTP (парсинг запроса, коды ответов) | **View** | бизнес-логику не держит |

---

## 2. Паттерны, уже принятые в проекте (канон — следовать им)

Эти решения — **эталон**. Новый код обязан им соответствовать.

### 2.1 Service Layer
`billing/services.py`: `WalletService`, `SubscriptionService`, `TrialService`.
Вся денежная логика — здесь, не во вьюхах. Новую денежную/бизнес-операцию
добавляем как метод сервиса, а не в `views.py`.

### 2.2 Ledger / Double-entry (деньги)
`billing.Transaction` — **append-only**: каждое движение денег = строка со
знаковой `amount` и снимком `balance_after`. Баланс кошелька денормализован
(`Wallet.balance`) ради скорости; источник правды — сумма транзакций. Ночная
сверка `reconcile_wallet_balances` (`balance == SUM(transactions)`).
**Правило: `Wallet.balance` менять ТОЛЬКО через `WalletService` — никаких
`balance += x` в коде.**

### 2.3 Idempotency Key
Каждая денежная операция несёт детерминированный `idempotency_key`
(UNIQUE-констрейнт): `multicard:<id>`, `sub-purchase:<id>`, `lesson-payout:<id>`,
`lesson-refund:<id>`, `trial-debit:<id>` и т.д. Повтор (ретрай callback, двойной
клик) не дублирует движение денег.
**Правило: любая новая денежная операция — с idempotency-ключом и проверкой
существования под локом.**

### 2.4 Pessimistic Lock + Unit of Work
`select_for_update` на кошельке/подписке/брони + `transaction.atomic()`.
Сериализует конкурентные операции одного пользователя (двойной клик, две
вкладки, гонка callback). Применять как **единую точку сериализации** в начале
atomic-блока (см. `WalletService._apply`, `SubscriptionService.pay`,
`TrialService.book_paid_trial`).

### 2.5 Snapshot (защита от изменения тарифа задним числом)
`Subscription` фиксирует `commission_rate`, `price_total`, `price_per_lesson`,
`total_lessons` в момент покупки. Изменение настроек/тарифа НЕ влияет на
купленные подписки. **Правило: при покупке снимаем снимок (snapshot) цены/условий, не
читаем «живой» тариф при расчёте по уже купленному.**

### 2.6 Reconciliation / Sweep (самовосстановление)
Celery-задачи дозакрывают «потерянные» состояния при сбоях между шагами:
`reconcile_orphaned_refunds`, `settle_expired_subscriptions`,
`release_pending_payouts`, `expire_unpaid_approvals`. **Правило: если денежный
шаг делается во view ПОСЛЕ коммита статуса — должен быть sweep, который подберёт
осиротевшее состояние** (иначе деньги зависают; это был баг #4).

### 2.7 Constraints как defense-in-depth
Бизнес-инварианты продублированы на уровне БД: `wallet_balance_non_negative`,
`one_active_booking_per_slot` (partial unique), `subscription_escrow_non_negative`,
case-insensitive unique email. **Правило: критичный инвариант — это и проверка в
сервисе, И констрейнт в БД (Python-проверку может обойти гонка).**

### 2.8 PROTECT на деньги-несущих связях
FK на сущности с финансовой историей — `on_delete=PROTECT` (Wallet, Transaction,
WithdrawalRequest, MulticardInvoice, Booking.student, TimeSlot.teacher).
**Правило: всё, что несёт деньги/аудит, — PROTECT, не CASCADE.**

---

## 3. Где паттернов не хватает (технический долг)

Асимметрия: `billing` сделан по Service Layer, `teachers` — нет.

| Проблема | Файл | Целевой паттерн |
|---|---|---|
| **Fat Model / God Object**: lifecycle броней с деньгами/уведомлениями в методах модели (`confirm/cancel_by_*/reschedule_by_student/settle_after_end`) | `teachers/models.py` (~3500 строк) | вынести в **`BookingService`** |
| **Логика отмены размазана по 3 слоям**: статус — модель, деньги — view, политика — сервис | `booking_views.py` + `models.py` + `services.py` | собрать в **`BookingService` (Unit of Work)** |
| **Fat Views**: 69 функций вперемешку | `teachers/views.py` (~3022 строки) | разбить на **пакет `views/`** по доменам |
| **Дублирование «активный урок пакета»** (≥3 разных определения) | `book_schedule`/`reschedule`/quota | **Single Source of Truth**: метод QuerySet |
| **Дублирование `Notification.objects.create`** (×4) | `booking_views.py` хелперы | **Notification Service / Factory** |
| **Переходы статусов «на глаз»** (нет явной таблицы) | `Booking.status`, `Subscription.status` | **State Machine** (ALLOWED_TRANSITIONS) |
| **Магические числа** (`PAYOUT_GRACE_HOURS, 24`; `join_grace 30`; `[:200]`) | разбросаны | константы в `settings`/модуле |
| **Широкий `except Exception`** трактует ошибку БД как «нет профиля» | `middleware.py:_user_needs_onboarding` | ловить `ObjectDoesNotExist` |

---

## 4. Целевые паттерны — где и зачем (карта)

| Паттерн | Назначение в проекте | Применить к |
|---|---|---|
| **Service Layer** | Бизнес-логика вне моделей/вьюх | `teachers/services.py` ← booking lifecycle |
| **State Machine** | Корректные, явные переходы статусов; нет «застрявших» состояний | `Booking`, `Subscription` |
| **Unit of Work** | Статус + деньги в одном `atomic` (а не статус в модели, деньги во view) | отмена/неявка/перенос |
| **Repository (QuerySet/Manager)** | Переиспользуемые запросы как методы | `BookingQuerySet.active_in_subscription()` и т.п. |
| **Gateway / Adapter** | Изоляция внешних API | `multicard.py` (формализовать), `JitsiGateway`, `notification_service` |
| **Factory** | Единая точка создания уведомлений (in-app + email + telegram) | расширить `notification_service` |
| **Strategy** | Политики (комиссия, прощение неявок, политика отмены) как явные стратегии | `cancel_lesson`, no-show |
| **Observer (Signals)** | Уже используется (`signals.py`) — для побочных эффектов (кэш, уведомления) | держать побочное, не бизнес-логику |

### Анти-паттерны / чего НЕ делать
- ❌ Не вводить Repository-абстракции **поверх** ORM «ради чистоты» — Django ORM уже Active Record + Repository.
- ❌ Не тащить CQRS / Event Sourcing / Hexagonal в монолит — оверинжиниринг.
- ❌ Не класть бизнес-логику в шаблоны и в `context_processors`.
- ❌ Не менять `Wallet.balance` мимо `WalletService`.
- ❌ Не плодить «умные» миксины с побочными эффектами в моделях.

---

## 5. Правила добавления нового кода (чек-лист для PR)

1. **Бизнес-операция?** → метод в `*/services.py`, не во view.
2. **Двигает деньги?** → `idempotency_key` + `select_for_update` + `atomic` + (если шаг после коммита статуса) sweep.
3. **Меняет статус?** → проверить допустимость перехода; деньги — в той же транзакции.
4. **Новый инвариант?** → проверка в сервисе **И** констрейнт в БД.
5. **FK на деньги/историю?** → `on_delete=PROTECT`.
6. **Внешний API?** → через Gateway/Adapter, с таймаутом и обработкой ошибки.
7. **Пользовательский текст в чат/уведомление?** → маскировка контактов (`contact_filter`) + экранирование (Markdown/HTML/`escapejs`).
8. **Запрос в цикле по объектам?** → `select_related`/`prefetch_related` (нет N+1).
9. **Тесты:** positive + negative + edge (двойной клик/гонка/пустые данные) + регрессия. Денежное — обязательно тест на идемпотентность и гонку.

---

## 6. Roadmap инкрементального рефакторинга (Strangler Fig)

Большой рефакторинг запрещён правилом «не ломать». Двигаться слоями, **строго
за зелёными тестами**, поведение не меняя:

**Фаза 0 — Характеризация (нулевой риск).**
Поднять покрытие тестами текущего lifecycle броней (тесты на «как есть»):
`confirm/cancel_by_student/cancel_by_teacher/reschedule/settle_after_end` во всех
ветках. Это страховочная сеть для последующих шагов.

**Фаза 1 — Извлечение `BookingService` (рефакторинг без смены поведения).**
Перенести тело методов из `Booking`-модели и из `booking_views.py` в
`teachers/services.py::BookingService`. Модель и view вызывают сервис (старые
точки входа сохраняются → обратная совместимость). URL/API не меняются.

**Фаза 2 — Unit of Work для денег.**
Денежную политику отмены/неявки/переноса перенести ВНУТРЬ atomic-перехода
(закрывает корень багов #4/#H2 «по-взрослому», а не sweep-заплаткой).

**Фаза 3 — State Machine.**
Когда логика в сервисе — ввести явные `ALLOWED_TRANSITIONS` для `Booking`/
`Subscription`, один `transition(to)`-метод. Убирает недостижимые/застрявшие
состояния.

**Фаза 4 — Гигиена.**
Разбить `teachers/views.py` на пакет `views/` по доменам (URL не трогаем).
Вынести магические числа в `settings`. Единый Notification Factory.

**Критерий готовности каждой фазы:** все тесты зелёные, поведение API
идентично, нет новых N+1, миграции (если есть) — обратимые `AlterField`.

---

## 7. Карта компонентов (быстрый справочник)

| Компонент | Файл | Слой |
|---|---|---|
| Деньги/escrow/payout | `billing/services.py` | Service ⭐ |
| Платёжный шлюз | `billing/multicard.py` | Gateway |
| Модели денег | `billing/models.py` | Model + Constraints |
| Sweep-задачи | `billing/tasks.py` | Background |
| Booking lifecycle (**рефакторинг-таргет**) | `teachers/models.py`, `teachers/booking_views.py` | Model+View → Service |
| Чат realtime | `teachers/consumers.py` | Entry (WS) |
| Анти-обход контактов | `teachers/contact_filter.py` | Domain util |
| Уведомления | `telegram_bot/notification_service.py` | Gateway/Factory |
| Лиды | `teachers/leads.py` | Domain (тонкий, без таблицы) ⭐ |
| Побочные эффекты | `teachers/signals.py`, `billing/signals.py` | Observer |

---

_Документ живой. При изменении архитектурных решений — обновлять здесь, а не
заводить параллельные описания. Связанные файлы: `PROJECT_DOCUMENTATION.md`
(функциональное описание), `deploy/DEPLOY.md` (инфраструктура),
`docs/MULTICARD_INTEGRATION.md` (платёжный шлюз)._
