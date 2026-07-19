"""Финансовый фундамент платформы.

Wallet — денормализованный баланс пользователя.
Transaction — append-only ledger; источник правды для аудита.

Инвариант: wallet.balance == SUM(transactions[wallet, status=completed].amount).
Все мутации balance делать ТОЛЬКО через billing.services.WalletService.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


# ---------- Wallet ----------------------------------------------------------


class Wallet(models.Model):
    """Кошелёк пользователя.

    Один на каждого пользователя (auto-create через post_save signal).
    Хранит денормализованный balance — для быстрого чтения. Источник правды
    при расхождении — Transaction ledger (см. WalletService.reconcile_balance()).
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='wallet',
        verbose_name=_('Пользователь'),
    )
    balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name=_('Баланс'),
        help_text=_('Денормализованный баланс. Источник правды — Transaction ledger.'),
    )
    currency = models.CharField(
        max_length=3,
        default='UZS',
        verbose_name=_('Валюта'),
    )
    last_transaction_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Последняя транзакция'),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Кошелёк')
        verbose_name_plural = _('Кошельки')
        constraints = [
            models.CheckConstraint(
                check=models.Q(balance__gte=Decimal('0')),
                name='wallet_balance_non_negative',
            ),
        ]

    def __str__(self) -> str:
        return f'Wallet#{self.pk} {self.user_id} balance={self.balance} {self.currency}'


# ---------- Transaction (ledger) -------------------------------------------


class Transaction(models.Model):
    """Запись в финансовом журнале (append-only).

    Положительные amount = пополнение, отрицательные = списание.
    Идемпотентность гарантируется UNIQUE-полем idempotency_key.
    """

    class Type(models.TextChoices):
        # Пополнения (amount > 0)
        DEPOSIT = 'deposit', _('Пополнение баланса')
        REFUND = 'refund', _('Возврат за неиспользованные уроки')
        LESSON_PAYOUT = 'lesson_payout', _('Выплата учителю за урок')
        COMMISSION = 'commission', _('Комиссия платформы (доход)')
        ADJUSTMENT_IN = 'adjustment_in', _('Корректировка (зачисление)')

        # Списания (amount < 0)
        PURCHASE = 'purchase', _('Покупка подписки')
        WITHDRAWAL = 'withdrawal', _('Вывод средств')
        COMMISSION_DEDUCT = 'commission_deduct', _('Удержание комиссии')
        ADJUSTMENT_OUT = 'adjustment_out', _('Корректировка (списание)')

    class Status(models.TextChoices):
        PENDING = 'pending', _('В обработке')
        COMPLETED = 'completed', _('Завершена')
        REVERSED = 'reversed', _('Отменена')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(
        Wallet,
        on_delete=models.PROTECT,
        related_name='transactions',
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text=_('Знаковая сумма: > 0 — зачисление, < 0 — списание.'),
    )
    balance_after = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text=_('Снимок баланса кошелька после применения транзакции.'),
    )
    type = models.CharField(max_length=32, choices=Type.choices)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.COMPLETED,
    )
    idempotency_key = models.CharField(
        max_length=128,
        unique=True,
        help_text=_('Уникальный ключ операции. Повторный вызов с тем же ключом — no-op.'),
    )
    related_booking = models.ForeignKey(
        'teachers.Booking',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    related_subscription = models.ForeignKey(
        'billing.Subscription',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transactions',
    )
    reference = models.CharField(
        max_length=128,
        blank=True,
        default='',
        help_text=_('Внешний ID операции (например, payment_id от Payme/Click).'),
    )
    description = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Финансовая транзакция')
        verbose_name_plural = _('Финансовые транзакции')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['wallet', '-created_at']),
            # Подсчёт заработка учителя/комиссии по кошельку и типу.
            models.Index(fields=['wallet', 'type', 'status']),
            # Доход платформы/выплаты за период (revenue по типу+статусу+дате).
            models.Index(fields=['type', 'status', '-created_at']),
            models.Index(fields=['related_booking']),
            models.Index(fields=['related_subscription']),
        ]

    def __str__(self) -> str:
        sign = '+' if self.amount > 0 else ''
        return f'Tx#{self.id} {sign}{self.amount} {self.type} wallet={self.wallet_id}'


# ---------- Tariff (предложение учителя) -----------------------------------


class Tariff(models.Model):
    """Тариф (subscription package) — то, что учитель предлагает ученикам.

    Пример: «Английский, 2 урока в неделю по 60 мин, 1 месяц, 800 000 сум».

    Производные величины (всегда вычисляются «на лету», не хранятся):
      * total_lessons = lessons_per_week * 4 * duration_months   (4 недели/месяц)
      * total_price   = price_per_month * duration_months
      * price_per_lesson = total_price / total_lessons

    Изменение цены/количества уроков в тарифе НЕ влияет на уже купленные
    подписки — там зафиксирован snapshot.
    """

    LESSONS_PER_WEEK_CHOICES = [(i, f'{i} в неделю') for i in (1, 2, 3, 4, 5)]
    DURATION_MINUTES_CHOICES = [
        (30, _('30 минут')),
        (45, _('45 минут')),
        (60, _('60 минут')),
        (90, _('90 минут')),
    ]
    DURATION_MONTHS_CHOICES = [
        (1, _('1 месяц')),
        (2, _('2 месяца')),
        (3, _('3 месяца')),
        (6, _('6 месяцев')),
        (12, _('12 месяцев')),
    ]

    teacher = models.ForeignKey(
        'teachers.TeacherProfile',
        on_delete=models.CASCADE,
        related_name='tariffs',
        verbose_name=_('Учитель'),
    )
    subject = models.ForeignKey(
        'teachers.Subject',
        on_delete=models.PROTECT,
        related_name='tariffs',
        verbose_name=_('Предмет'),
    )

    name = models.CharField(
        max_length=80,
        blank=True,
        default='',
        help_text=_('Опционально: «Базовый», «Стандарт», «Премиум».'),
    )
    description = models.TextField(
        blank=True,
        default='',
        help_text=_('Чему ученик научится за этот тариф (опционально).'),
    )

    lessons_per_week = models.PositiveSmallIntegerField(
        choices=LESSONS_PER_WEEK_CHOICES,
        default=2,
    )
    lesson_duration_minutes = models.PositiveSmallIntegerField(
        choices=DURATION_MINUTES_CHOICES,
        default=60,
    )
    duration_months = models.PositiveSmallIntegerField(
        choices=DURATION_MONTHS_CHOICES,
        default=1,
        help_text=_('Срок подписки в месяцах (минимум 1).'),
    )
    price_per_month = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text=_('Цена за 1 месяц этого тарифа в сумах.'),
    )

    is_active = models.BooleanField(
        default=True,
        help_text=_('Если выключен — нельзя купить, но активные подписки продолжают работать.'),
    )
    is_recommended = models.BooleanField(
        default=False,
        help_text=_('Учитель помечает один тариф как рекомендованный — выделяется в UI.'),
    )
    sort_order = models.PositiveSmallIntegerField(
        default=0,
        help_text=_('Порядок отображения (меньше = выше).'),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Тариф')
        verbose_name_plural = _('Тарифы')
        ordering = ['sort_order', '-is_recommended', 'price_per_month']
        indexes = [
            models.Index(fields=['teacher', 'is_active']),
            models.Index(fields=['subject', 'is_active']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(price_per_month__gt=0),
                name='tariff_price_positive',
            ),
            models.CheckConstraint(
                check=models.Q(lessons_per_week__gte=1, lessons_per_week__lte=7),
                name='tariff_lessons_per_week_range',
            ),
            models.CheckConstraint(
                check=models.Q(duration_months__gte=1, duration_months__lte=24),
                name='tariff_duration_months_range',
            ),
        ]

    WEEKS_PER_MONTH = 4

    @property
    def total_lessons(self) -> int:
        return self.lessons_per_week * self.WEEKS_PER_MONTH * self.duration_months

    @property
    def total_price(self) -> Decimal:
        return (self.price_per_month * self.duration_months).quantize(Decimal('0.01'))

    @property
    def price_per_lesson(self) -> Decimal:
        if self.total_lessons == 0:
            return Decimal('0.00')
        return (self.total_price / self.total_lessons).quantize(Decimal('0.01'))

    def __str__(self) -> str:
        label = self.name or f'{self.lessons_per_week}/нед.'
        return f'{label} · {self.subject} · {self.teacher_id}'


# ---------- Subscription (купленный тариф / активная подписка) -------------


class Subscription(models.Model):
    """Купленная подписка ученика на учителя.

    Snapshot подхода: все цены/количества фиксируются в момент покупки.
    Изменение исходного Tariff НЕ влияет на уже активные Subscription.
    Money flow:
      escrow_balance = price_total
      После каждого проведённого урока:
        teacher.wallet += price_per_lesson * (1 - commission_rate)
        platform.wallet += price_per_lesson * commission_rate
        escrow_balance -= price_per_lesson
      При отмене: escrow_balance возвращается на student.wallet.
    """

    class Status(models.TextChoices):
        PENDING_APPROVAL = 'pending_approval', _('Ожидает подтверждения учителя')
        PENDING_PAYMENT = 'pending_payment', _('Одобрена, ожидает оплаты')
        ACTIVE = 'active', _('Активна')
        PAUSED = 'paused', _('Приостановлена')
        COMPLETED = 'completed', _('Завершена (все уроки)')
        EXPIRED = 'expired', _('Истёкла (срок вышел)')
        CANCELLED_BY_STUDENT = 'cancelled_by_student', _('Отменена учеником')
        CANCELLED_BY_TEACHER = 'cancelled_by_teacher', _('Отклонена/отменена учителем')
        CANCELLED_BY_ADMIN = 'cancelled_by_admin', _('Отменена администрацией')

    # Активные статусы — нельзя создать вторую заявку/подписку с тем же
    # учителем и предметом, пока есть незавершённая в одном из этих статусов.
    ACTIVE_STATUSES = (
        Status.PENDING_APPROVAL,
        Status.PENDING_PAYMENT,
        Status.ACTIVE,
        Status.PAUSED,
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='subscriptions',
    )
    teacher = models.ForeignKey(
        'teachers.TeacherProfile',
        on_delete=models.PROTECT,
        related_name='subscriptions',
    )
    subject = models.ForeignKey(
        'teachers.Subject',
        on_delete=models.PROTECT,
        related_name='subscriptions',
    )
    tariff = models.ForeignKey(
        Tariff,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subscriptions',
        help_text=_('Из какого тарифа куплено. Может быть NULL, если тариф удалён.'),
    )

    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.PENDING_PAYMENT,
        db_index=True,
    )

    # ---- Snapshot (immutable после создания) ----
    lessons_per_week = models.PositiveSmallIntegerField()
    lesson_duration_minutes = models.PositiveSmallIntegerField()
    duration_months = models.PositiveSmallIntegerField()
    total_lessons = models.PositiveIntegerField(
        help_text=_('Сколько уроков всего по этой подписке.'),
    )
    price_total = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text=_('Полная стоимость подписки в момент покупки.'),
    )
    price_per_lesson = models.DecimalField(
        max_digits=14, decimal_places=2,
        help_text=_('Стоимость одного урока = price_total / total_lessons.'),
    )
    commission_rate = models.DecimalField(
        max_digits=5, decimal_places=4,
        help_text=_('Доля платформы в payout (0..1). Snapshot на момент покупки.'),
    )

    # ---- Счётчики (меняются по ходу подписки) ----
    escrow_balance = models.DecimalField(
        max_digits=14, decimal_places=2,
        default=Decimal('0.00'),
        help_text=_('Сколько денег ещё лежит в эскроу платформы по этой подписке.'),
    )
    completed_lessons = models.PositiveIntegerField(default=0)
    lessons_paid_out = models.PositiveIntegerField(
        default=0,
        help_text=_('Сколько уроков уже выплачено учителю (после grace window).'),
    )

    # ---- Лимит переносов (на подписку, окно — календарный месяц) ----
    reschedules_used = models.PositiveSmallIntegerField(default=0)
    reschedules_period = models.CharField(
        max_length=7, blank=True, default='',
        help_text=_('Месяц действия счётчика переносов в формате YYYY-MM.'),
    )

    # ---- Даты ----
    started_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True, default='')

    # ---- Flow «заявка → одобрение → оплата → бронь» (ТЗ) ----
    approved_at = models.DateTimeField(
        null=True, blank=True,
        help_text=_('Когда учитель подтвердил заявку на обучение.'),
    )
    approval_expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text=_('Дедлайн оплаты одобренной заявки (после — EXPIRED).'),
    )
    preferred_schedule = models.TextField(
        blank=True, default='',
        help_text=_('Предпочтительное расписание/пожелания ученика из заявки.'),
    )
    weekly_pattern = models.JSONField(
        null=True, blank=True, default=None,
        help_text=_('Подтверждённый недельный шаблон броней: [{"day":"monday","time":"18:00"}, ...].'),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Защита от двойного клика «Купить» — UNIQUE на ключе покупки.
    purchase_idempotency_key = models.CharField(
        max_length=128,
        unique=True,
        help_text=_('Гарантирует один Subscription при повторных submit.'),
    )

    class Meta:
        verbose_name = _('Подписка')
        verbose_name_plural = _('Подписки')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['student', 'status']),
            models.Index(fields=['teacher', 'status']),
            models.Index(fields=['status', '-created_at']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(escrow_balance__gte=Decimal('0')),
                name='subscription_escrow_non_negative',
            ),
            models.CheckConstraint(
                check=models.Q(total_lessons__gt=0),
                name='subscription_total_lessons_positive',
            ),
            models.CheckConstraint(
                check=models.Q(commission_rate__gte=Decimal('0'))
                & models.Q(commission_rate__lte=Decimal('1')),
                name='subscription_commission_rate_0_1',
            ),
            # Defense-in-depth к app-guard `AlreadySubscribed` в create_request:
            # БД не даст создать вторую незавершённую подписку с тем же учителем
            # и предметом, даже при гонке мимо application-уровня.
            models.UniqueConstraint(
                fields=['student', 'teacher', 'subject'],
                condition=models.Q(status__in=[
                    'pending_approval', 'pending_payment', 'active', 'paused',
                ]),
                name='uniq_active_subscription_per_triple',
            ),
        ]

    @property
    def remaining_lessons(self) -> int:
        return max(0, self.total_lessons - self.completed_lessons)

    @property
    def progress_percent(self) -> int:
        if self.total_lessons == 0:
            return 0
        return int(round(100 * self.completed_lessons / self.total_lessons))

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def remaining_payout_total(self) -> Decimal:
        """Сколько ещё может получить учитель по этой подписке (чистыми)."""
        per_lesson_net = self.price_per_lesson * (Decimal('1') - self.commission_rate)
        return (per_lesson_net * (self.total_lessons - self.lessons_paid_out)).quantize(Decimal('0.01'))

    @property
    def teacher_earned_so_far(self) -> Decimal:
        """Сколько чистыми получил учитель по этой подписке (за вычетом комиссии)."""
        per_lesson_net = self.price_per_lesson * (Decimal('1') - self.commission_rate)
        return (per_lesson_net * self.lessons_paid_out).quantize(Decimal('0.01'))

    @property
    def platform_earned_so_far(self) -> Decimal:
        """Сколько комиссии получила платформа по этой подписке."""
        per_lesson_comm = self.price_per_lesson * self.commission_rate
        return (per_lesson_comm * self.lessons_paid_out).quantize(Decimal('0.01'))

    # ---- Progress aggregations (Phase 9) ----
    #
    # Эти свойства часто рендерятся СПИСКОМ (страница прогресса) → раньше каждое
    # било в БД на каждую подписку (N+1). Теперь они считаются в Python из
    # ОДНОГО списка броней/ДЗ: если вью сделал prefetch_related('bookings__slot',
    # 'homeworks') — запросов 0; если нет — список грузится один раз и кэшируется
    # на инстансе (одна загрузка вместо нескольких COUNT'ов). Результаты идентичны.

    def _cached_bookings(self):
        cache = getattr(self, '_bookings_cache', None)
        if cache is None:
            # Если вью сделал prefetch_related('bookings__slot') — берём из кэша
            # (0 запросов). Иначе грузим один раз со slot одним запросом.
            if 'bookings' in getattr(self, '_prefetched_objects_cache', {}):
                cache = list(self.bookings.all())
            else:
                cache = list(self.bookings.select_related('slot').all())
            self._bookings_cache = cache
        return cache

    def _cached_homeworks(self):
        cache = getattr(self, '_homeworks_cache', None)
        if cache is None:
            cache = list(self.homeworks.all())
            self._homeworks_cache = cache
        return cache

    @property
    def attendance_rate(self) -> int:
        """Процент посещаемости: completed / (completed + missed) × 100."""
        bookings = self._cached_bookings()
        finished = [b for b in bookings
                    if b.status in ('completed', 'no_show_student', 'no_show_teacher')]
        if not finished:
            return 0
        completed = sum(1 for b in finished if b.status == 'completed')
        return int(round(100 * completed / len(finished)))

    @property
    def homework_total(self) -> int:
        return len(self._cached_homeworks())

    @property
    def homework_graded(self) -> int:
        return sum(1 for h in self._cached_homeworks() if h.status == 'graded')

    @property
    def homework_completion_rate(self) -> int:
        """Процент проверенных ДЗ от всех заданных."""
        total = self.homework_total
        if total == 0:
            return 0
        return int(round(100 * self.homework_graded / total))
    

    @property
    def average_grade(self):
        """Средняя оценка по проверенным ДЗ (0-100). None если нет оценок."""
        from django.db.models import Avg
        from .models import HomeworkSubmission
        result = HomeworkSubmission.objects.filter(
            homework__subscription=self,
            grade__isnull=False,
        ).aggregate(avg=Avg('grade'))
        avg = result['avg']
        return round(avg, 1) if avg is not None else None

    @property
    def lessons_this_week(self) -> int:
        """Сколько уроков (всех статусов) на этой неделе."""
        from django.utils import timezone as tz
        from datetime import timedelta
        now = tz.now()
        monday = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        sunday = monday + timedelta(days=7)
        return sum(1 for b in self._cached_bookings()
                   if b.slot and monday <= b.slot.start_at < sunday)

    @property
    def next_lesson(self):
        """Ближайший предстоящий confirmed-урок (или None)."""
        from django.utils import timezone as tz
        now = tz.now()
        upcoming = [b for b in self._cached_bookings()
                    if b.status == 'confirmed' and b.slot and b.slot.start_at >= now]
        return min(upcoming, key=lambda b: b.slot.start_at) if upcoming else None

    @property
    def learning_streak_weeks(self) -> int:
        """Сколько ПОСЛЕДОВАТЕЛЬНЫХ недель подряд (от текущей назад) у ученика
        был хотя бы один completed-урок. 0 = на этой неделе нет.
        """
        from django.utils import timezone as tz
        from datetime import timedelta
        now = tz.now()
        monday_this = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        weeks_with_lesson = set()
        for b in self._cached_bookings():
            if b.status != 'completed' or not b.slot:
                continue
            iso = b.slot.start_at.date().isocalendar()
            weeks_with_lesson.add((iso.year, iso.week))

        streak = 0
        cursor = monday_this
        while True:
            iso = cursor.date().isocalendar()
            if (iso.year, iso.week) in weeks_with_lesson:
                streak += 1
                cursor -= timedelta(days=7)
            else:
                break
        return streak

    def __str__(self) -> str:
        return (
            f'Sub#{str(self.id)[:8]} {self.student_id}→{self.teacher_id} '
            f'{self.subject} {self.status} ({self.completed_lessons}/{self.total_lessons})'
        )


# ---------- WithdrawalRequest (вывод средств учителем) ---------------------


class WithdrawalRequest(models.Model):
    """Заявка учителя на вывод средств.

    Money flow:
      1. user создаёт заявку → wallet -= amount (WITHDRAWAL type), status=pending
         (средства уже «зарезервированы» — нельзя их использовать на покупки)
      2. admin approve → status=approved (банк-перевод ещё не сделан)
      3. admin переводит реальные деньги → mark completed
      4. ИЛИ admin reject → status=rejected, wallet += amount (REFUND)
      5. ИЛИ user cancel (пока pending) → wallet += amount
    """

    class Status(models.TextChoices):
        PENDING = 'pending', _('Ожидает подтверждения')
        APPROVED = 'approved', _('Одобрена (перевод в процессе)')
        COMPLETED = 'completed', _('Выполнена')
        REJECTED = 'rejected', _('Отклонена')
        CANCELLED = 'cancelled', _('Отменена пользователем')

    class PayoutMethod(models.TextChoices):
        CARD = 'card', _('Карта (UzCard / Humo / Visa)')
        PHONE = 'phone', _('На номер телефона')
        OTHER = 'other', _('Другое')

    OPEN_STATUSES = (Status.PENDING, Status.APPROVED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='withdrawal_requests',
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    payout_method = models.CharField(
        max_length=10,
        choices=PayoutMethod.choices,
        default=PayoutMethod.CARD,
    )
    payout_details = models.CharField(
        max_length=200,
        help_text=_('Номер карты / номер телефона / другие реквизиты.'),
    )
    comment = models.TextField(blank=True, default='', max_length=500)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='+',
        help_text=_('Админ, который последним менял статус.'),
    )
    admin_note = models.TextField(blank=True, default='', max_length=1000)

    reviewed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    idempotency_key = models.CharField(
        max_length=128,
        unique=True,
        help_text=_('Защита от двойного submit формы.'),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Заявка на вывод средств')
        verbose_name_plural = _('Заявки на вывод средств')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['status', '-created_at']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=Decimal('0')),
                name='withdrawal_amount_positive',
            ),
        ]

    def __str__(self) -> str:
        return f'Wd#{str(self.id)[:8]} {self.user_id} {self.amount} {self.status}'


# ---------- Homework / LMS (Phase 8) ---------------------------------------


def _homework_upload_path(instance, filename: str) -> str:
    """Файлы домашек хранятся в media/homework/<homework_uuid>/<filename>"""
    hw_id = getattr(instance, 'homework_id', None) or getattr(instance, 'pk', None) or 'tmp'
    return f'homework/{hw_id}/{filename}'


def _submission_upload_path(instance, filename: str) -> str:
    sub_id = getattr(instance, 'submission_id', None) or 'tmp'
    return f'homework/submissions/{sub_id}/{filename}'


class Homework(models.Model):
    """Домашнее задание учителя ученику в рамках подписки.

    Жизненный цикл:
      assigned   → ученик ещё не сдал
      submitted  → ученик сдал, ждёт проверки
      graded     → учитель оценил
      returned   → учитель вернул на доработку (ученик может пересдать)
    """

    class Status(models.TextChoices):
        ASSIGNED = 'assigned', _('Задано')
        SUBMITTED = 'submitted', _('Сдано на проверку')
        GRADED = 'graded', _('Проверено')
        RETURNED = 'returned', _('Возвращено на доработку')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    subscription = models.ForeignKey(
        'billing.Subscription',
        on_delete=models.CASCADE,
        related_name='homeworks',
    )
    # Денормализация для скорости запросов в дашбордах.
    teacher = models.ForeignKey(
        'teachers.TeacherProfile',
        on_delete=models.CASCADE,
        related_name='homeworks_assigned',
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='homeworks_received',
    )

    title = models.CharField(max_length=200)
    description = models.TextField(max_length=5000)
    due_at = models.DateTimeField(null=True, blank=True,
                                   help_text=_('Опциональный дедлайн.'))
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ASSIGNED,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Когда отправлено напоминание о приближении дедлайна — чтобы не слать дважды.
    reminder_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Домашнее задание')
        verbose_name_plural = _('Домашние задания')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['subscription', '-created_at']),
            models.Index(fields=['teacher', 'status']),
            models.Index(fields=['student', 'status']),
        ]

    def __str__(self) -> str:
        return f'HW#{str(self.id)[:8]} {self.title} → {self.student_id} ({self.status})'

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone as tz
        return (self.due_at is not None
                and self.due_at < tz.now()
                and self.status in (self.Status.ASSIGNED, self.Status.RETURNED))


class HomeworkAttachment(models.Model):
    """Файлы, прикреплённые учителем к заданию."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    homework = models.ForeignKey(
        Homework, on_delete=models.CASCADE, related_name='attachments',
    )
    file = models.FileField(upload_to=_homework_upload_path)
    filename = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(help_text=_('Размер в байтах'))
    mime_type = models.CharField(max_length=80, blank=True, default='')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Файл задания')
        verbose_name_plural = _('Файлы заданий')

    def __str__(self) -> str:
        return f'{self.filename} ({self.file_size}B)'


class HomeworkSubmission(models.Model):
    """Ответ ученика на ДЗ. OneToOne — одно задание = один submission."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    homework = models.OneToOneField(
        Homework, on_delete=models.CASCADE, related_name='submission',
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='homework_submissions',
    )
    text_response = models.TextField(max_length=5000, blank=True, default='')

    grade = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[],  # 0..100, проверим на уровне формы
        help_text=_('Оценка от 0 до 100.'),
    )
    feedback = models.TextField(max_length=2000, blank=True, default='')

    submitted_at = models.DateTimeField(auto_now_add=True)
    graded_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Сдача ДЗ')
        verbose_name_plural = _('Сдачи ДЗ')
        constraints = [
            models.CheckConstraint(
                check=models.Q(grade__isnull=True) | (models.Q(grade__gte=0) & models.Q(grade__lte=100)),
                name='hw_submission_grade_range',
            ),
        ]

    def __str__(self) -> str:
        return f'Submission #{str(self.id)[:8]} hw={self.homework_id} grade={self.grade}'


class HomeworkSubmissionFile(models.Model):
    """Файлы ответа ученика."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(
        HomeworkSubmission, on_delete=models.CASCADE, related_name='files',
    )
    file = models.FileField(upload_to=_submission_upload_path)
    filename = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField()
    mime_type = models.CharField(max_length=80, blank=True, default='')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Файл сдачи')
        verbose_name_plural = _('Файлы сдач')

    def __str__(self) -> str:
        return f'{self.filename} ({self.file_size}B)'


# ---------- LessonDispute (спор по уроку, ТЗ шаг 8) ------------------------


class LessonDispute(models.Model):
    """Спор ученика по проведённому уроку (период проверки до выплаты учителю).

    Пока спор OPEN — выплата учителю по этому уроку заморожена (см.
    release_lesson_payout / release_trial_payout). Админ решает:
      * resolve_refund   — деньги возвращаются ученику (refund_lesson/refund_trial);
      * resolve_rejected — спор отклонён, выплата уходит учителю.
    """

    class Status(models.TextChoices):
        OPEN = 'open', _('Открыт')
        RESOLVED_REFUND = 'resolved_refund', _('Решён в пользу ученика (возврат)')
        RESOLVED_REJECTED = 'resolved_rejected', _('Отклонён (выплата учителю)')
        CANCELLED = 'cancelled', _('Отозван учеником')

    OPEN_STATUSES = (Status.OPEN,)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.OneToOneField(
        'teachers.Booking', on_delete=models.CASCADE, related_name='dispute',
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='disputes',
    )
    reason = models.TextField(max_length=2000)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True,
    )
    admin_note = models.TextField(blank=True, default='', max_length=1000)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Спор по уроку')
        verbose_name_plural = _('Споры по урокам')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self) -> str:
        return f'Dispute#{str(self.id)[:8]} booking={self.booking_id} {self.status}'


# ---------- DismissedTrialSuggestion ----------------------------------------


class DismissedTrialSuggestion(models.Model):
    """Ученик скрыл предложение «Продолжить обучение» после пробного урока.

    Если после пробного урока учитель не понравился, ученик нажимает «Убрать»
    на карточке конверсии в дашборде. Запись (student, teacher, subject) гасит
    эту рекомендацию навсегда — она больше не показывается.
    """

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='dismissed_trial_suggestions',
    )
    teacher = models.ForeignKey(
        'teachers.TeacherProfile',
        on_delete=models.CASCADE,
        related_name='+',
    )
    subject = models.ForeignKey(
        'teachers.Subject',
        on_delete=models.CASCADE,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Скрытое предложение пробного')
        verbose_name_plural = _('Скрытые предложения пробного')
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'teacher', 'subject'],
                name='uniq_dismissed_trial_per_student_teacher_subject',
            ),
        ]
        indexes = [
            models.Index(fields=['student']),
        ]

    def __str__(self) -> str:
        return f'Dismissed trial: student={self.student_id} teacher={self.teacher_id} subject={self.subject_id}'


# ---------- Multicard (онлайн-пополнение кошелька) --------------------------


class MulticardInvoice(models.Model):
    """Инвойс Multicard на онлайн-пополнение кошелька.

    Один объект = одна попытка оплаты. Наш `id` (UUID) уходит в Multicard как
    invoice_id, их идентификатор приходит в `multicard_uuid`. Связанная DEPOSIT-
    транзакция создаётся при успешном callback (идемпотентно).
    """

    class Status(models.TextChoices):
        # Статусы платежа Multicard (поле status в callback / PaymentModel).
        DRAFT = 'draft', _('Черновик')
        PROGRESS = 'progress', _('В обработке')
        SUCCESS = 'success', _('Оплачен')
        ERROR = 'error', _('Ошибка')
        REVERT = 'revert', _('Возврат')
        HOLD = 'hold', _('Холдирование')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # PROTECT — платёжный аудит (card_pan/receipt_url/raw_callback/paid_at)
        # нельзя терять при удалении User. Консистентно с Wallet/Transaction/
        # WithdrawalRequest (все деньги PROTECT). Удаление пользователя с
        # инвойсами должно блокироваться, а не молча уносить историю платежей.
        on_delete=models.PROTECT,
        related_name='multicard_invoices',
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text=_('Сумма пополнения в сумах (UZS).'),
    )
    store_id = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PROGRESS,
    )
    # Идентификатор транзакции на стороне Multicard.
    multicard_uuid = models.CharField(max_length=64, blank=True, default='', db_index=True)
    checkout_url = models.URLField(max_length=512, blank=True, default='')
    short_link = models.CharField(max_length=255, blank=True, default='')
    # Данные из callback (для аудита/чека).
    card_pan = models.CharField(max_length=32, blank=True, default='')
    ps = models.CharField(max_length=32, blank=True, default='')
    receipt_url = models.URLField(max_length=512, blank=True, default='')
    # Зачисление в кошелёк (создаётся при первом success-callback).
    transaction = models.ForeignKey(
        'billing.Transaction',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    raw_callback = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Инвойс Multicard')
        verbose_name_plural = _('Инвойсы Multicard')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status']),
        ]

    def __str__(self) -> str:
        return f'MulticardInvoice {self.id} user={self.user_id} {self.amount} {self.status}'


# ---------- BookingDeposit (депозит за разовый урок) ------------------------


class BookingDeposit(models.Model):
    """Депозит за разовую (не подписочную) бронь — анти-неявка.

    Один бесплатный пробный на ученика; после него любая разовая бронь требует
    депозита. Депозит — не доп. платёж, а сама оплата разового урока:
      * PENDING  — запись создана, средства ещё не удержаны (транзиентное);
      * PAID     — сумма удержана с кошелька ученика (debit) при бронировании;
      * USED     — урок состоялся: депозит выплачен учителю (минус комиссия);
      * FORFEITED — ученик не пришёл: депозит сгорел, ушёл учителю (не возвращается);
      * REFUNDED — вина учителя / урок не состоялся / отмена: возвращён ученику.

    Денежное движение выполняет billing.deposits.DepositService через
    WalletService (единственный путь мутации баланса). Эта модель — журнал
    состояния депозита и снапшот суммы; сумма фиксируется на момент брони, чтобы
    смена BOOKING_DEPOSIT_AMOUNT не переписывала историю.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', _('Ожидает оплаты')
        PAID = 'paid', _('Внесён (удержан)')
        USED = 'used', _('Зачтён в оплату урока')
        FORFEITED = 'forfeited', _('Сгорел (неявка)')
        REFUNDED = 'refunded', _('Возвращён')

    # Терминальные статусы — депозит отыгран, дальнейших переходов нет.
    TERMINAL_STATUSES = (Status.USED, Status.FORFEITED, Status.REFUNDED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.OneToOneField(
        'teachers.Booking',
        on_delete=models.CASCADE,
        related_name='deposit',
        verbose_name=_('Бронь'),
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        verbose_name=_('Сумма депозита'),
        help_text=_('Снапшот суммы на момент бронирования.'),
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Момент перехода в терминальный статус (used/forfeited/refunded).
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Депозит за урок')
        verbose_name_plural = _('Депозиты за уроки')
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=Decimal('0')),
                name='bookingdeposit_amount_positive',
            ),
        ]
        indexes = [
            models.Index(fields=['status']),
        ]

    def __str__(self) -> str:
        return f'BookingDeposit {self.id} booking={self.booking_id} {self.amount} {self.status}'

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES
