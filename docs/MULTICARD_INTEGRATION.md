# Multicard в UstozHub — как это работает и где снимаются реальные деньги

> Дата: 2026-06-05. Статус: интеграция реализована и проверена живым платежом на sandbox.
> Документация шлюза: https://docs.multicard.uz/

---

## 1. Краткий ответ на главный вопрос

**Реальные деньги с карты клиента снимаются РОВНО в одном месте:**

> на **хостинговой странице оплаты Multicard** (`checkout_url`, домен `*.multicard.uz` / `app.rhmt.uz`), когда пользователь **пополняет кошелёк**.

Всё остальное внутри платформы — это **внутренние движения по балансу кошелька** (в сумах, UZS), а не списания с карты:

- покупка подписки — внутреннее списание с баланса кошелька (`PURCHASE`);
- эскроу и выплаты учителю — внутренние переводы по кошелькам;
- вывод средств учителем (`WithdrawalRequest`) — отдельный ручной/админский процесс, **не через Multicard**.

То есть Multicard — это **единственный вход реальных денег** в систему (пополнение кошелька). Карта клиента дебетуется только Multicard'ом на его странице; наш сервер карту никогда не видит и не хранит.

```
┌─────────────┐   реальные деньги    ┌──────────────────┐   внутренний баланс (UZS)   ┌──────────────┐
│   Карта     │ ───────────────────► │  Кошелёк (Wallet) │ ─────────────────────────► │  Подписка     │
│  клиента    │   (через Multicard   │   баланс в сумах  │   списание PURCHASE         │  (эскроу)     │
└─────────────┘    checkout-страницу)└──────────────────┘                             └──────┬───────┘
        ▲                                                                                     │ после уроков
        │ ЕДИНСТВЕННОЕ место списания                                                         ▼
   с реальной карты                                                            ┌──────────────────────────┐
                                                                               │ Кошелёк учителя + комиссия │
                                                                               │       платформы            │
                                                                               └──────────────────────────┘
```

---

## 2. Модель денег в UstozHub (контекст)

В проекте деньги учитываются через append-only журнал:

| Модель | Назначение |
|--------|-----------|
| `Wallet` | Денормализованный баланс пользователя в сумах. Один на пользователя. |
| `Transaction` | Журнал всех операций. `amount > 0` — зачисление, `< 0` — списание. Идемпотентность по `idempotency_key`. |
| `Subscription` | Купленный тариф. Поле `escrow_balance` — деньги, удерживаемые платформой до проведения уроков. |
| `MulticardInvoice` | **Новое.** Инвойс на онлайн-пополнение через Multicard (трекинг + аудит). |

Инвариант: `wallet.balance == SUM(transactions[completed].amount)`.
Все изменения баланса идут **только** через `billing.services.WalletService` (атомарно, с блокировкой строки кошелька).

Multicard встроен как **провайдер пополнения кошелька**. Эскроу, подписки и выплаты при интеграции **не менялись**.

---

## 3. Полный поток онлайн-пополнения (где и что происходит)

### Шаг 0. Конфигурация
Ключи читаются из `.env` в `core/settings.py` (блок `MULTICARD_*`). Суммы в API Multicard — в **тийинах** (1 сум = 100 тийин).

### Шаг 1. Пользователь инициирует пополнение
- Страница: `GET /my/wallet/topup/` → `billing.views.wallet_topup_request`.
- Шаблон `templates/billing/topup_request.html` показывает блок **«Оплатить картой онлайн»** (если `MULTICARD_ENABLED`).
- Пользователь вводит сумму (в сумах) и жмёт «Перейти к оплате» → `POST /my/wallet/topup/multicard/`.

### Шаг 2. Сервер создаёт инвойс (реальных денег ЕЩЁ НЕТ)
View `billing.views.wallet_topup_multicard`:
1. Валидирует сумму (`MULTICARD_MIN_TOPUP` … `MULTICARD_MAX_TOPUP`).
2. Создаёт запись `MulticardInvoice` (статус `progress`, наш `UUID` = `invoice_id` для Multicard).
3. Через `MulticardClient.create_invoice(...)` делает:
   - `POST /auth` → получает JWT-токен (кешируется ~23 ч);
   - `POST /payment/invoice` с заголовком **`Authorization: Bearer <token>`** и телом:
     `store_id`, `amount` (тийины), `invoice_id`, `callback_url`, `ofd` (фискальные данные), `return_url`, `lang`.
4. Multicard возвращает `uuid` (их ID платежа) и **`checkout_url`**.
5. Сервер сохраняет `multicard_uuid` / `checkout_url` в `MulticardInvoice` и делает **редирект на `checkout_url`**.

> На этом этапе деньги НЕ списаны. Создан только «счёт».

### Шаг 3. 💳 РЕАЛЬНОЕ СПИСАНИЕ ДЕНЕГ
- Пользователь на **странице Multicard** (`checkout_url`) вводит карту, проходит 3-D Secure / OTP.
- **Именно здесь Multicard списывает реальные деньги с карты.** Наш сервер в этом не участвует и карту не видит.
- Деньги поступают на счёт мерчанта в Multicard (привязан к `store_id` приложения).

### Шаг 4. Multicard уведомляет наш сервер (callback / webhook)
- Multicard шлёт `POST` на `callback_url` = `https://<домен>/payments/multicard/callback/`
  (маршрут в `core/urls.py`, **вне** i18n, без языкового префикса; с IP `195.158.26.90`).
- View `billing.views.multicard_callback` (`@csrf_exempt`):
  1. **Проверяет подпись** `sign` = `MD5(store_id + invoice_id + amount + secret)` (см. §5).
     Неверная подпись → `400` (и Multicard ретраит/отменяет).
  2. Находит `MulticardInvoice` по `invoice_id`.
  3. **Сверяет сумму** из callback (тийины) с заявленной. Несовпадение → `400`.
  4. Определяет успех: `status == 'success'` **или** (поля `status` нет, но есть `payment_time`).
  5. При успехе вызывает `_credit_invoice(...)`:
     - `WalletService.credit(DEPOSIT, idempotency_key="multicard:<invoice.id>", reference=<multicard_uuid>)`
       — **зачисляет сумму на баланс кошелька** (внутренние деньги в сумах);
     - помечает инвойс `success`, проставляет `paid_at`, `card_pan`, `ps`, `receipt_url`, сырой `raw_callback`.
  6. Отвечает **HTTP 200** (обязательно — иначе Multicard до 5 раз ретраит и может отменить успешный платёж).

> Идемпотентность: ключ `multicard:<invoice.id>` гарантирует, что повторные callback'и (ретраи) **не задвоят** зачисление.

### Шаг 5. Возврат пользователя
- После оплаты Multicard возвращает пользователя на `return_url` = `/my/wallet/topup/return/?invoice=<id>`.
- View `wallet_topup_return`: показывает результат. Если callback ещё не пришёл — делает **best-effort** запрос `GET /payment/<uuid>` и при `status=success` зачисляет сам (та же идемпотентная функция).

### Диаграмма последовательности

```
Пользователь        Наш сервер (Django)            Multicard
     │  POST /topup/multicard/  │                       │
     │ ───────────────────────► │  POST /auth           │
     │                          │ ────────────────────► │
     │                          │  POST /payment/invoice│
     │                          │ ────────────────────► │
     │                          │ ◄──── checkout_url ─── │
     │ ◄──── 302 redirect ───── │                       │
     │ ───────────── открывает checkout_url ──────────► │
     │                💳 ВВОД КАРТЫ + OTP → СПИСАНИЕ ──► │  (реальные деньги)
     │                          │ ◄─ POST callback ──── │  (sign, amount, card_pan…)
     │                          │  verify sign + credit │
     │                          │ ──── 200 OK ────────► │
     │ ◄──── 302 return_url ──── (после оплаты) ──────── │
```

---

## 4. Где деньги «двигаются» дальше (уже без карты)

После того как кошелёк пополнен реальными деньгами:

1. **Покупка подписки** — `SubscriptionService.pay()` (`billing/services.py`):
   - проверяет `wallet.balance >= price_total`;
   - **списывает** с кошелька `Transaction.PURCHASE`;
   - переводит сумму в `subscription.escrow_balance` (удержание платформой);
   - подписка → `ACTIVE`.
2. **Проведение уроков → выплата учителю** — Celery-задача `release_pending_payouts` (`billing/tasks.py`):
   - из эскроу: `price_per_lesson × (1 − commission)` → кошелёк учителя, остаток → комиссия платформы.
3. **Вывод средств учителем** — `WithdrawalRequest` (ручной/админский процесс). **Не через Multicard** (Payouts API Multicard в проекте пока не подключён).

> Вывод: реальные деньги **входят** через Multicard (пополнение) и **выходят** при выводе средств учителю (отдельный процесс). Внутри — только баланс в сумах.

---

## 5. Подпись callback (важно — документация врёт)

Формула выяснена **эмпирически** на sandbox (доки указывают иначе):

```
sign = MD5( store_id + invoice_id + amount + secret )
```

- `store_id` — **числовой** store_id из тела callback (напр. `6`), НЕ наш UUID-магазина;
- `amount` — целое число тийинов;
- `secret` — `MULTICARD_SECRET` приложения;
- всё конкатенируется в строку без разделителей, затем `md5(...).hexdigest()`.

Проверено на реальном callback:
`MD5("6" + "843c597c-…" + "40000000" + "<secret>")` = `7f676883dabdc00fcccd8931b878efbd` ✅

Реализация: `billing/multicard.py` → `compute_sign()` / `verify_sign()` (сравнение через `hmac.compare_digest`).

### Два вида callback от Multicard
| Тип | Поле `status` | Признак | Как трактуем |
|-----|---------------|---------|--------------|
| callback-success | **отсутствует** | есть `payment_time`, `card_pan`, `receipt_url` | успех → зачисляем |
| webhook | есть | `draft/progress/success/error/revert/hold` | по значению `status` |

---

## 6. Заголовок авторизации (тоже не как в доках)

Эндпоинты `/payment/*` требуют **`Authorization: Bearer <token>`**.
С `X-Access-Token` (как в примерах документации) → `403 RBAC: access denied`.

---

## 7. Карта файлов

| Файл | Что внутри |
|------|------------|
| `billing/multicard.py` | `MulticardClient` (auth+кеш токена, create/get/delete invoice), `compute_sign`/`verify_sign`, хелперы `sum_to_tiyin`/`tiyin_to_sum`, `build_topup_ofd`. |
| `billing/models.py` | Модель `MulticardInvoice` (+ миграция `0013_multicardinvoice`). |
| `billing/views.py` | `wallet_topup_multicard`, `multicard_callback`, `wallet_topup_return`, `_credit_invoice`. |
| `core/urls.py` | Маршрут webhook `/payments/multicard/callback/` (вне i18n). |
| `billing/urls.py` | Маршруты `/my/wallet/topup/multicard/`, `/my/wallet/topup/return/`. |
| `templates/billing/topup_request.html` | Кнопка «Оплатить картой онлайн». |
| `templates/billing/topup_return.html` | Страница результата оплаты. |
| `core/settings.py` | Блок `MULTICARD_*`. |
| `billing/tests.py` | `MulticardSignTests`, `MulticardCallbackTests` (9 тестов). |

---

## 8. Настройки (`.env`)

| Переменная | Назначение |
|-----------|-----------|
| `MULTICARD_BASE_URL` | `https://dev-mesh.multicard.uz` (sandbox) / `https://mesh.multicard.uz` (prod) |
| `MULTICARD_APPLICATION_ID` | ID приложения |
| `MULTICARD_SECRET` | Секрет (используется в подписи) |
| `MULTICARD_STORE_ID` | UUID кассы (отправляется в инвойсе) |
| `MULTICARD_ENABLED` | Включить онлайн-оплату (авто-true при наличии ключей) |
| `MULTICARD_MIN_TOPUP` / `MULTICARD_MAX_TOPUP` | Лимиты суммы (в сумах) |
| `MULTICARD_OFD_MXIK` / `MULTICARD_OFD_PACKAGE_CODE` / `MULTICARD_OFD_NAME` / `MULTICARD_OFD_VAT_PERCENT` | Фискальные данные строки чека |
| `MULTICARD_CALLBACK_IP` | IP Multicard для логирования/whitelisting (`195.158.26.90`) |
| `SITE_URL` | Базовый домен для `callback_url` / `return_url` |

---

## 9. Безопасность и надёжность

- **Карта не касается нашего сервера** — ввод только на стороне Multicard (PCI-зона у них).
- **Подпись** проверяется на каждом callback (constant-time сравнение). Неверная → `400`.
- **Сверка суммы**: callback с суммой ≠ заявленной отклоняется.
- **Идемпотентность**: ключ `multicard:<invoice.id>` — ретраи не задваивают зачисление.
- **Ответ 2xx обязателен**: иначе Multicard ретраит и **отменяет** успешный платёж (это и была причина ранее наблюдавшегося «сервис недоступен»).
- **Атомарность зачисления**: `_credit_invoice` под `select_for_update` на инвойсе + кошелёк через `WalletService`.

---

## 10. Sandbox vs Production — чек-лист перед боем

- [ ] `MULTICARD_BASE_URL=https://mesh.multicard.uz`
- [ ] Боевые `MULTICARD_APPLICATION_ID` / `MULTICARD_SECRET` / `MULTICARD_STORE_ID`, `MULTICARD_ENABLED=true`
- [ ] **Реальные** `MULTICARD_OFD_MXIK` / `MULTICARD_OFD_PACKAGE_CODE` для услуги «пополнение кошелька» (сейчас стоят тестовые коды из доков)
- [ ] `SITE_URL` = боевой HTTPS-домен (для корректного `callback_url`)
- [ ] Сервер принимает `POST` с IP `195.158.26.90` на `/payments/multicard/callback/` (firewall/nginx)
- [ ] Кеш токена работает (Redis в проде) — токен не дёргается на каждый запрос

### Тестовые данные (sandbox)
- Карта: `8600533364098829`, срок `2806`, OTP `112233`

---

## 11. Что НЕ входит в текущую интеграцию

- Привязка карт (сохранение `card_token`) и оплата «в один клик» — не реализовано (только разовые оплаты через checkout).
- Прямая оплата подписки картой (в обход кошелька) — не реализовано; подписка платится с баланса.
- Payouts (выплаты на карту) через Multicard — не подключено; вывод средств учителю идёт отдельным процессом.
- Возвраты/частичные возвраты (refund/revert) через API — обрабатываются вручную (callback `revert` лишь помечает инвойс).
