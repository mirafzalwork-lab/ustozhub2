# ТЗ: Доработка биллинга, подписок и расписания UstozHub (v2)

> Источник: критический аудит бизнес-логики (PM / UX / System Architect / Founder).
> Документ описывает целевое поведение и порядок внедрения. Каждый шаг внедряется
> отдельно и проверяется smoke-тестом в двух ролях: **учитель** и **ученик**.

## 0. Что сохраняем (не трогаем)

Сильные стороны текущей архитектуры — оставляем без изменений:

- Ledger-модель `Transaction` (append-only) + `idempotency_key` (UNIQUE).
- Escrow на платформенном аккаунте (`PLATFORM_ACCOUNT_USERNAME`).
- `select_for_update` во всех денежных и слотовых операциях.
- Snapshot тарифа в `Subscription` (изменение `Tariff` не影响ает активные подписки).
- `UniqueConstraint(one_active_booking_per_slot)`.

## 1. Глоссарий статусов (целевой)

### Booking
`pending → confirmed → completed → (payout)`; ветки: `cancelled_by_student`,
`cancelled_by_teacher`, `expired`, `no_show_teacher`, `no_show_student`, `rescheduled`.

«Доставленные» статусы (учитель получает выплату): **`completed`, `no_show_student`**.
«Недоставленные» (возврат ученику): `no_show_teacher`, `cancelled_*`, `expired`.

### Subscription
`pending_approval → pending_payment → active ⇄ paused → completed | expired | cancelled_*`.

## 2. Порядок внедрения (P0 → P2)

### Шаг 1 (P0). Escrow-таймаут для зависших активных подписок
**Проблема:** нет задачи, закрывающей `ACTIVE`/`PAUSED` подписку после `expires_at`,
если уроки не проведены (бесконечные переносы/неявки) → escrow зависает.

**Требование:**
- Новый метод `SubscriptionService.settle_expired(subscription)`:
  - срабатывает только для `ACTIVE`/`PAUSED` с `expires_at < now − PAYOUT_GRACE_HOURS`;
  - доурегулирует прошедшие `confirmed` уроки (`settle_after_end` + payout);
  - отменяет будущие брони, освобождает слоты;
  - возвращает остаток escrow ученику (`Transaction.Type.REFUND`, ключ `sub-expire:{id}`);
  - финальный статус: `COMPLETED` если все уроки доставлены, иначе `EXPIRED`.
- Celery-задача `billing.settle_expired_subscriptions` (раз в час).
- Идемпотентность: повторный запуск не создаёт повторных проводок.

**Инвариант:** сумма денег в системе сохраняется до и после.

### Шаг 2 (P0). Семантика неявок
**Проблема:** `no_show_student` не выставляется; если никто не зашёл — учитель
наказан как `no_show_teacher`.

**Требование:**
- `settle_after_end()` для Jitsi-уроков:
  - учитель не зашёл → `no_show_teacher` (возврат ученику);
  - учитель зашёл, ученик нет → `no_show_student` (**урок доставлен, выплата учителю**);
  - оба зашли → `completed`.
- Выплатные пути (`release_pending_payouts`, `release_lesson_payout`,
  `cancel`, `settle_expired`) считают `no_show_student` доставленным уроком.
- `_refund_teacher_no_show` срабатывает только для `no_show_teacher`.

### Шаг 3 (P0). Перенос не убивает оплаченный урок
**Проблема:** `reschedule_by_student()` сбрасывает `confirmed` → `pending`,
требуя повторного подтверждения; без дедлайна и лимита.

**Требование:**
- Перенос брони, входящей в оплаченную подписку, на слот в подтверждённой
  доступности учителя → сразу `confirmed` (без повторного подтверждения).
- Дедлайн переноса: не позже `RESCHEDULE_MIN_LEAD_HOURS` (env, default 4) до начала.
- Лимит: `SUBSCRIPTION_FREE_RESCHEDULES_PER_MONTH` переносов на подписку в месяц.
- Старый статус фиксируется как `rescheduled` в истории (аудит).

### Шаг 4 (P1). Недельная квота уроков
**Проблема:** лимит «N уроков/неделю» не enforced (только отображение).

**Требование:** при бронировании урока внутри подписки проверять
`lessons_this_week < lessons_per_week` под локом; иначе 409.

### Шаг 5 (P1). Политика отмен с дедлайном
**Проблема:** отмена всегда 100% возврат — эксплуатируемо.

**Требование:** отмена урока подписки:
- `> CANCELLATION_FULL_REFUND_HOURS` (default 24) до начала → урок возвращается в квоту;
- `≤` порога → урок списывается (выплата учителю по обычному grace).

### Шаг 6 (P1). Pause / Resume подписки
**Требование:** методы `pause()`/`resume()`; на паузе уроки не генерируются,
`expires_at` сдвигается на длительность паузы.

### Шаг 7 (P1). Мастер миграции броней при смене расписания учителя
**Требование:** при изменении `weekly_schedule` показать конфликтующие брони,
предложить перенос/уведомить учеников (вместо жёсткого 409).

### Шаг 8 (P2). Рост
Upgrade/downgrade тарифа, автопродление (`next_billing_date`, `auto_renew`),
увеличенное окно спора, дедлайн ответа учителя в споре.

## 3. Конфигурация (новые константы settings)

| Константа | Default | Назначение |
|---|---|---|
| `RESCHEDULE_MIN_LEAD_HOURS` | 4 | мин. запас до урока для переноса |
| `CANCELLATION_FULL_REFUND_HOURS` | 24 | порог полного возврата при отмене |
| `SUBSCRIPTION_FREE_RESCHEDULES_PER_MONTH` | 2 | (уже есть) лимит переносов |
| `PAYOUT_GRACE_HOURS` | 24→**48** | окно спора (увеличить) |

## 4. Тестирование (после каждого шага)

Для каждого шага — smoke-скрипт `scripts/v2_stepN_smoke.py`, прогоняющий сценарий
**за учителя и за ученика** end-to-end, + полный `manage.py test teachers billing`.
Проверяется денежный инвариант (сумма по кошелькам + escrow + pending withdrawals
не меняется на нейтральных операциях).
