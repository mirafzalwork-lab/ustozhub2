# Production-аудит UstozHub — 2026-06-07

Комплексный аудит силами «команды»: QA, Product, UX/UI, Security, Full-Stack. Метод — параллельный анализ 6 направлений (карта проекта, безопасность, биллинг, уроки/брони, i18n, UX/производительность) с верификацией находок чтением кода и тестами.

**Состояние тестов после правок:** `CELERY_TASK_ALWAYS_EAGER=1 manage.py test teachers.tests_booking_lifecycle billing.tests` → **187 OK** (до правок — 2 ошибки). `manage.py check` — чисто.

Легенда статуса: ✅ исправлено в этой сессии · ⏳ требует отдельного прохода (описано решение).

---

## КРИТИЧЕСКИЕ

### ✅ K1. `leave_review` падал 500 на любом отзыве с комментарием
- **Описание:** `comment, _ = mask_contacts(comment)` переопределял локальную `_`, затеняя gettext `_`; следующий `messages.success(_('Спасибо!...'))` → `TypeError: 'bool' object is not callable`.
- **Причина:** использование `_` как throwaway-переменной в модуле, где `_` = `gettext`.
- **Влияние:** любой ученик, оставивший отзыв с текстом, получал 500; отзыв при этом сохранялся (двусмысленный UX). Отзывы — ключевой trust-механизм.
- **Решение:** переименовал в `_masked`; превентивно убрал такое же затенение `_` в `booking_views.py` (строки 728/847/1033/1111), т.к. при i18n-обёртке booking API они стали бы 500. `teachers/booking_views.py:1455`.

### ✅ K2. Двойное бронирование слота при генерации расписания подписки
- **Описание:** `_generate_bookings_from_pattern` и `_generate_bookings_for_subscription` выбирали слот `filter(status='free').first()` без `select_for_update`.
- **Причина:** отсутствие блокировки строки слота внутри `transaction.atomic`.
- **Влияние:** два ученика, одновременно бронирующие один свободный слот, оба проходили проверку; второй падал `IntegrityError` (500) по `UniqueConstraint` вместо аккуратного пропуска; во втором методе ещё и создавались дубли/пересекающиеся `TimeSlot`.
- **Решение:** добавил `select_for_update()` к выборке слота в обеих функциях. `billing/services.py:729, 1440`. ⏳ Остаётся (см. С-блок): `_generate_bookings_for_subscription` создаёт слоты без overlap-проверки — рекомендую прогонять через ту же валидацию, что `slots_create_api`.

---

## ВЫСОКИЙ ПРИОРИТЕТ

### ✅ H1. Урок подписки `not_held`: деньги зависали в эскроу на недели
- **Описание:** когда к уроку никто не подключился, для платного пробного делался возврат, а для урока подписки — нет (эскроу «вернётся при закрытии подписки»). Квота при этом освобождалась.
- **Влияние:** деньги ученика заперты до `settle_expired` (часы–недели); если перебронировать некуда — ученик не мог ни заниматься, ни получить деньги.
- **Решение:** `_handle_not_held` теперь вызывает `SubscriptionService.refund_lesson(...)` (идемпотентно, возврат на кошелёк + уменьшение пакета), как для no-show учителя. `teachers/tasks.py:426`.

### ✅ H2. Рассинхрон окон присутствия (room vs attendance)
- **Описание:** `lesson_room` открывает комнату за `LESSON_JOIN_LEAD_MINUTES` (10) до старта, а `lesson_attendance_api` принимал beacon `join` только с `start−15`.
- **Влияние:** при `LESSON_JOIN_LEAD_MINUTES > 15` сторона входит в комнату, но `join` отклоняется 409 → `join_at` не пишется → `settle_after_end` считает присутствовавшего за no-show и **ошибочно трогает деньги**.
- **Решение:** окно attendance выровнено по `LESSON_JOIN_LEAD_MINUTES`. `teachers/booking_views.py:1396`.

### ✅ H3. Сломанное создание брони / `ModuleNotFoundError: django_ratelimit`
- **Описание/решение:** пакет был в `requirements.txt`, но не установлен в venv. Установлен `django-ratelimit==4.1.0` (+ позже `celery==5.4.0` для полноты окружения и тестов). Это локальный пробел, на проде воспроизводиться не должен.

### ⏳ H4 (Security). Файлы ДЗ отдаются из публичного `/media/` без авторизации
- **Описание:** вложения и сдачи ДЗ хранятся как `FileField` под `media/homework/<uuid>/...`; nginx (`deploy/nginx.conf:70`) отдаёт `/media/` напрямую без аутентификации. Защита — только «неугадываемый» UUID в пути, бессрочно.
- **Влияние:** утечка приватных работ ученика/материалов учителя по расшаренной/залогированной ссылке. (Файлы УРОКОВ сделаны правильно — через короткоживущие S3 presigned + проверку участника.)
- **Решение:** отдавать ДЗ через авторизованную Django-view с проверкой `_user_role_for_homework` и `X-Accel-Redirect` на `internal;`-локацию (как уже сделано для `/media/certificates/`), либо перевести на S3 presigned + `Content-Disposition: attachment`. `billing/models.py:736`.

### ⏳ H5 (Security). Multicard: жёсткая проверка подписи есть, но IP-whitelist не блокирует
- **Описание:** `multicard_callback` при несовпадении IP только логирует (`# Не блокируем жёстко`). Единственный барьер против поддельного callback — `verify_sign` (MD5+secret) и кросс-проверка суммы. Идемпотентность спасает от повторов, но не от первого фрода при утечке/слабости секрета.
- **Решение:** при заданном `MULTICARD_CALLBACK_IP` возвращать 403 на mismatch (с корректным разбором `X-Forwarded-For` за прокси); перепроверить алгоритм подписи против актуальной prod-доки; удалить отладочный `find_sign_formula` из прод-кода. `billing/views.py:306`, `billing/multicard.py:54`.

### ⏳ H6 (Security). `wallet_topup_return` (GET) зачисляет деньги
- **Описание:** GET-страница возврата на best-effort пути вызывает `_credit_invoice`. Прямой эксплуатации нет (инвойс привязан к `request.user`, идемпотентно, сумма сверяется с `get_payment`), но мутация баланса в GET хрупка (префетч/сканеры/перезагрузки).
- **Решение:** зачислять ТОЛЬКО в подписанном webhook `multicard_callback`; на return-странице лишь читать статус (pending → «обрабатывается»). `billing/views.py:378`.

### ⏳ H7 (i18n). Весь booking/calendar/lesson API — только на русском
- **Описание:** ~56 сообщений `_json_error(...)` и `HttpResponseForbidden(...)` в `teachers/booking_views.py` не обёрнуты в `_()`. UZ/EN-пользователь видит ошибки бронирования/календаря/комнаты только по-русски.
- **Решение:** обернуть в `_()` (импорт уже есть). ⚠️ Перед этим уже убрано затенение `_` (см. K1), так что обёртка безопасна. Затем `makemessages --ignore=venv` + перевод. `teachers/booking_views.py` (строки перечислены в i18n-отчёте агента).

### ⏳ H8 (i18n). 72 fuzzy-перевода в en и uz — семантически НЕВЕРНЫ
- **Описание:** `msgmerge` нечётко сопоставил строки; напр. «Максимальная сумма пополнения…» → en «The minimum withdrawal amount…». Это не устаревшие, а вводящие в заблуждение переводы.
- **Решение:** просмотреть и переснять все 72 fuzzy в `locale/{en,uz}/LC_MESSAGES/django.po`, снять флаг `#, fuzzy`, перекомпилировать.

---

## СРЕДНИЙ ПРИОРИТЕТ

### ✅ M1 (billing). Поздняя отмена ученика не инкрементила `completed_lessons`
- **Описание:** при поздней отмене урок засчитывался учителю (`lessons_paid_out++`), но сигнал `_on_completed` не учитывает `cancelled_by_student` → `completed_lessons` отставал.
- **Влияние:** `progress_percent`, `attendance_rate`, квота при `resume` (`total_lessons − completed_lessons`) считались неверно (деньги корректны, счётчики — нет).
- **Решение:** в `release_lesson_payout` для `cancelled_by_student` инкрементим `completed_lessons`. `billing/services.py:1262`.

### ⏳ M2 (billing). Двойной платный пробный при гонке
- **Описание:** `book_paid_trial` не идемпотентен по ключу запроса; два одновременных POST на РАЗНЫЕ слоты одного учителя проходят `_existing_trial_qs` (локи не пересекаются) → два booking + два дебета, обход «один пробный на пару».
- **Решение:** partial-`UniqueConstraint` «один незакрытый trial на (student, teacher)» на уровне БД, либо сериализация по `Wallet.select_for_update(user=student)` с проверкой `_existing_trial_qs` под локом. `billing/services.py:1675`.

### ⏳ M3 (lessons). ДЗ можно сдавать/оценивать после отмены/истечения подписки
- **Описание:** `homework_detail` проверяет роль и статус ДЗ, но не статус подписки. Расходится с `teacher_homework_create` (фильтрует `ACTIVE_STATUSES`).
- **Решение:** guard `if homework.subscription.status not in Subscription.ACTIVE_STATUSES: deny` в submit/grade. `billing/views.py:1080`.

### ⏳ M4 (lessons). Накрутка рейтинга несколькими отзывами по одной подписке
- **Описание:** `unique_together` снят намеренно (per-booking отзыв) — один ученик с тарифом на N уроков может оставить N верифицированных пятёрок, все входят в `Avg('rating')`.
- **Решение:** в агрегате рейтинга считать один вес на пару teacher-student (среднее по ученику), `total_reviews` = число уникальных учеников. `teachers/signals.py:254`.

### ⏳ M5 (lessons). `mark_completed_lessons` — единая точка отказа money-flow
- **Описание:** если Celery beat не работает, `confirmed`-уроки навсегда висят: нет выплат, отзывов, эскроу заморожен. Нет алерта.
- **Решение:** health-алерт на «зависшие confirmed после end_at+grace»; страховочный settle.

### ⏳ M6 (perf). N+1 `average_grade` на странице прогресса ученика
- **Описание:** property делает свежий `aggregate(Avg)` на каждую подписку (×2 в шаблоне) — до ~30 лишних SQL.
- **Решение:** аннотировать подписки `Avg('homeworks__submission__grade')` во `my_progress` или считать из prefetch. `billing/models.py:570`, `billing/views.py:1213`.

### ⏳ M7 (perf). N+1 `get_teachers_count` в subject-эндпоинтах и `get_min_price` на главной
- **Описание:** на холодном кэше — 1 COUNT/MIN на каждый предмет/карточку (автокомплит дёргается на каждое нажатие).
- **Решение:** один запрос с `annotate(Count(..., filter=...))` / `annotate(Min('teachersubject__hourly_rate'))`. `teachers/views.py:2148`, `templates/logic/home.html:2237`.

### ⏳ M8 (i18n). 13 пустых переводов (en+uz) + отсутствие plural/ngettext
- **Описание:** новые строки Multicard/файлов/онлайн-оплаты не переведены; `ngettext` не используется нигде (0 вхождений), склонения «N уроков/дней» захардкожены; валюта `сум` не переведена для uz `so'm`/en `UZS`.
- **Решение:** заполнить 13 пустых msgstr; ввести `blocktrans count`/`ngettext` для счётных форм; обернуть `LESSONS_PER_WEEK_CHOICES` (`billing/models.py:185`).

### ⏳ M9 (security). Анти-обход контактов обходится; `video_presigned_url_register` без auth/throttle
- **Решение:** нормализация unicode перед матчингом + флагирование подозрительных сообщений; rate-limit по IP на регистрацию presigned-URL. `teachers/contact_filter.py`, `teachers/video_views.py:203`.

---

## НИЗКИЙ ПРИОРИТЕТ

- **L1 (lessons).** `reschedule_by_student` не проверяет совпадение длительности нового слота. `teachers/models.py:3051`.
- **L2 (lessons).** `slots_bulk_delete_api?only_free=false` каскадно удаляет held/booked слоты вместе с Booking без возврата эскроу. Запретить удаление занятых. `booking_views.py:529`.
- **L3 (lessons).** Пауза подписки переводит будущие уроки в `expired` (семантически неверно для аналитики). Ввести отдельный статус. `billing/services.py:1085`.
- **L4 (billing).** Срок подписки `30×months` дней vs биллинг `4 недели/мес` — рассинхрон «месяца» (к потере денег не ведёт). `services.py:332`.
- **L5 (billing).** Нет `MAX_WITHDRAWAL_AMOUNT`/дневного лимита и гарда «одна OPEN-заявка на вывод». `services.py:1493`.
- **L6 (perf).** Списки избранного без пагинации (`my_favorite_*`). `teachers/views.py:1571`.
- **L7 (UX).** Эмодзи вместо иконок: `emails/lesson_reminder.html:56` (🎥), `home.html:2119/2124` (★ в легенде). Заменить на FontAwesome.
- **L8 (UX).** Много инлайн-`style=` с захардкоженными цветами (`teacher_detail.html`, `student_progress.html`) — вынести в CSS-переменные/классы.
- **L9 (i18n).** Полностью русские страницы: `teacher_profile_edit.html`, `logout.html`, `base_wizard.html`; админ-шаблоны (~300 строк) — отдельный i18n-проход.
- **L10 (security).** `get_client_ip` доверяет `X-Forwarded-For` (для дедупа просмотров) — спуфится, но решений безопасности на этом нет.

---

## Что проверено и работает хорошо (подтверждено кодом)
- **IDOR:** все денежные/booking-объекты scoped по владельцу; UUID-PK; явные path-prefix гарды на lesson/video файлах. Критических Broken Access Control не найдено.
- **CSRF:** только webhooks `@csrf_exempt`, оба с проверкой подписи. **XSS:** DOM-синки через `escapeHtml`; `|safe` только на серверном JSON. **SQLi:** нет raw/extra/cursor. **Секреты:** не в репозитории.
- **Кошелёк:** debit/credit атомарны (`select_for_update` + CheckConstraint `balance≥0`), идемпотентны по `idempotency_key`; ночная сверка инварианта. Комиссия в `Decimal` с `quantize`.
- **Брони:** `UniqueConstraint(one_active_booking_per_slot)`, lock в `create_hold`, запрет брони в прошлом, no-show окна/прощение 3 за 90 дней, reschedule-лимиты — консистентны.
- **Производительность:** основные списки имеют `select_related`/`prefetch`/пагинацию/кэш; индексы покрывают частые фильтры. Чат-листы без N+1.

---

## Рекомендованный порядок follow-up
1. **H4** (auth на файлы ДЗ) — реальная утечка приватных данных.
2. **H5/H6** (Multicard: IP-блок + перенос зачисления только в webhook).
3. **H7+H8** (i18n booking API + чистка 72 неверных fuzzy) — самый заметный для UZ/EN пользователей слой.
4. **K2-остаток/M2/M3** (overlap-валидация слотов подписки, идемпотентность пробного, guard статуса подписки в ДЗ).
5. **M6/M7** (N+1), **M4** (рейтинг), затем L-косметика и админ-i18n.
