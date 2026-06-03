"""Атомарные финансовые операции над Wallet с записью в Transaction-ledger.

Все операции:
  * выполняются внутри `transaction.atomic()`
  * блокируют строку Wallet через `select_for_update()` для race-safety
  * пишут запись Transaction (источник правды для аудита)
  * идемпотентны по `idempotency_key` (повторный вызов возвращает существующую транзакцию)
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dt_time, timedelta
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import LessonDispute, Subscription, Tariff, Transaction, Wallet, WithdrawalRequest

User = get_user_model()

logger = logging.getLogger(__name__)


def _safe_reverse(name, args=None):
    """reverse, не падающий если URL ещё не подключён (для уведомлений)."""
    from django.urls import reverse
    try:
        return reverse(name, args=args or [])
    except Exception:
        return ''


# Дни недели для weekly_schedule (соответствуют datetime.weekday() 0..6).
_WEEKDAY_KEYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


class InsufficientFunds(Exception):
    """На кошельке недостаточно средств для дебета."""


class WalletService:
    """Все операции с балансом ходят через этот сервис, а не через `Wallet.balance += …`."""

    @staticmethod
    def get_or_create_wallet(user) -> Wallet:
        wallet, _ = Wallet.objects.get_or_create(user=user)
        return wallet

    @classmethod
    def credit(
        cls,
        *,
        user,
        amount: Decimal,
        tx_type: str,
        idempotency_key: str,
        description: str = '',
        related_booking=None,
        reference: str = '',
    ) -> Transaction:
        """Зачислить средства. Положительная транзакция."""
        amount = Decimal(amount)
        if amount <= 0:
            raise ValueError('credit amount must be positive')
        return cls._apply(
            user=user,
            signed_amount=amount,
            tx_type=tx_type,
            idempotency_key=idempotency_key,
            description=description,
            related_booking=related_booking,
            reference=reference,
        )

    @classmethod
    def debit(
        cls,
        *,
        user,
        amount: Decimal,
        tx_type: str,
        idempotency_key: str,
        description: str = '',
        related_booking=None,
        reference: str = '',
        allow_negative: bool = False,
    ) -> Transaction:
        """Списать средства. Отрицательная транзакция. Бросает InsufficientFunds, если не хватает."""
        amount = Decimal(amount)
        if amount <= 0:
            raise ValueError('debit amount must be positive')
        return cls._apply(
            user=user,
            signed_amount=-amount,
            tx_type=tx_type,
            idempotency_key=idempotency_key,
            description=description,
            related_booking=related_booking,
            reference=reference,
            allow_negative=allow_negative,
        )

    @classmethod
    def transfer(
        cls,
        *,
        from_user,
        to_user,
        amount: Decimal,
        tx_type_out: str,
        tx_type_in: str,
        idempotency_key: str,
        description: str = '',
        related_booking=None,
    ) -> tuple[Transaction, Transaction]:
        """Перевод между двумя кошельками. Атомарно: дебет источника + кредит назначения."""
        if from_user.pk == to_user.pk:
            raise ValueError('cannot transfer to the same wallet')

        # Внешняя транзакция: debit и credit либо оба применяются, либо ни один.
        # Без неё сбой между ними уничтожал бы деньги (списано, но не зачислено).
        with transaction.atomic():
            out_tx = cls.debit(
                user=from_user,
                amount=amount,
                tx_type=tx_type_out,
                idempotency_key=f'{idempotency_key}:out',
                description=description,
                related_booking=related_booking,
            )
            in_tx = cls.credit(
                user=to_user,
                amount=amount,
                tx_type=tx_type_in,
                idempotency_key=f'{idempotency_key}:in',
                description=description,
                related_booking=related_booking,
            )
            return out_tx, in_tx

    @classmethod
    def _apply(
        cls,
        *,
        user,
        signed_amount: Decimal,
        tx_type: str,
        idempotency_key: str,
        description: str,
        related_booking,
        reference: str,
        allow_negative: bool = False,
    ) -> Transaction:
        if not idempotency_key:
            raise ValueError('idempotency_key is required')

        existing = Transaction.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing

        with transaction.atomic():
            wallet = (
                Wallet.objects.select_for_update()
                .select_related('user')
                .get(user=user)
            )
            new_balance = wallet.balance + signed_amount
            if not allow_negative and new_balance < 0:
                raise InsufficientFunds(
                    f'wallet#{wallet.pk} balance {wallet.balance} cannot go negative '
                    f'with delta {signed_amount}'
                )

            try:
                tx = Transaction.objects.create(
                    wallet=wallet,
                    amount=signed_amount,
                    balance_after=new_balance,
                    type=tx_type,
                    status=Transaction.Status.COMPLETED,
                    idempotency_key=idempotency_key,
                    description=description,
                    related_booking=related_booking,
                    reference=reference,
                )
            except IntegrityError:
                # Гонка по idempotency_key: другой процесс успел создать ту же
                # транзакцию между select и create → возвращаем её. Но если это
                # НЕ дубль ключа (например, нарушение другого constraint) —
                # пробрасываем реальную ошибку, а не маскируем под идемпотентность.
                existing = Transaction.objects.filter(idempotency_key=idempotency_key).first()
                if existing is not None:
                    return existing
                raise

            wallet.balance = new_balance
            wallet.last_transaction_at = timezone.now()
            wallet.save(update_fields=['balance', 'last_transaction_at', 'updated_at'])
            return tx

    @staticmethod
    def reconcile_balance(wallet: Wallet) -> Decimal:
        """Пересчитать balance из всех Transaction. Возвращает то, что должно быть."""
        from django.db.models import Sum
        result = Transaction.objects.filter(
            wallet=wallet,
            status=Transaction.Status.COMPLETED,
        ).aggregate(total=Sum('amount'))
        return result['total'] or Decimal('0')


# ---------- SubscriptionService -------------------------------------------


class SubscriptionPurchaseError(Exception):
    """Базовая ошибка покупки подписки."""


class AlreadySubscribed(SubscriptionPurchaseError):
    """У ученика уже есть активная подписка к этому учителю по этому предмету."""


class TariffNotPurchasable(SubscriptionPurchaseError):
    """Тариф неактивен или удалён."""


class NotEnoughCapacity(SubscriptionPurchaseError):
    """У учителя в weekly_schedule недостаточно времени, чтобы расписать N уроков."""


class PayoutError(Exception):
    """Ошибка при попытке начислить payout учителю."""


class CancellationError(Exception):
    """Ошибка при попытке отменить подписку."""


class WithdrawalError(Exception):
    """Базовая ошибка при работе с заявкой на вывод."""


class WithdrawalAmountError(WithdrawalError):
    """Сумма заявки нарушает min/max ограничения."""


class SubscriptionService:
    """Покупка/отмена подписок с эскроу-логикой и автогенерацией Booking'ов.

    Покупка АТОМАРНА: либо всё (списание + Subscription + N bookings + слоты заняты),
    либо ничего.
    """

    # Сколько недель смотрим вперёд при поиске свободных слотов под подписку.
    # Должно покрывать duration_months×4 с запасом на пропущенные дни.
    LOOKAHEAD_WEEKS_MULTIPLIER = 2  # = 2× от плана недель

    @classmethod
    def purchase(
        cls,
        *,
        student,
        tariff: Tariff,
        idempotency_key: str,
    ) -> Subscription:
        """Купить подписку на тариф.

        Steps (атомарно):
          1. Проверить, что у ученика нет активной подписки с этим teacher+subject.
          2. Списать price_total с wallet ученика (InsufficientFunds → raise).
          3. Создать Subscription со snapshot.
          4. Сгенерировать N bookings из weekly_schedule учителя (включая free слоты
             или создавая новые TimeSlot'ы).
          5. Привязать списание к подписке (related_subscription).
        """
        if not idempotency_key:
            raise ValueError('idempotency_key is required')

        # Идемпотентность: если уже есть Subscription с этим ключом — вернуть её.
        existing = Subscription.objects.filter(
            purchase_idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            return existing

        if not tariff.is_active:
            raise TariffNotPurchasable('тариф неактивен')

        teacher = tariff.teacher
        subject = tariff.subject

        # Проверка weekly_schedule.
        if not teacher.has_schedule:
            raise NotEnoughCapacity('у учителя не указано расписание')

        with transaction.atomic():
            # 1) Конкурентная защита от двойной подписки student+teacher+subject.
            # Берём lock на student.wallet (косвенно — student-scope сериализация).
            wallet = (
                Wallet.objects.select_for_update()
                .get(user=student)
            )

            already = Subscription.objects.filter(
                student=student,
                teacher=teacher,
                subject=subject,
                status__in=Subscription.ACTIVE_STATUSES,
            ).exists()
            if already:
                raise AlreadySubscribed(
                    'У вас уже есть активная подписка к этому учителю по этому предмету.'
                )

            # 2) Snapshot цен и параметров.
            price_total = tariff.total_price
            total_lessons = tariff.total_lessons
            price_per_lesson = tariff.price_per_lesson
            commission_rate = Decimal(settings.PLATFORM_COMMISSION_RATE)

            if wallet.balance < price_total:
                from .services import InsufficientFunds  # noqa
                raise InsufficientFunds(
                    f'Недостаточно средств: на балансе {wallet.balance}, нужно {price_total}.'
                )

            now = timezone.now()
            expires_at = now + timedelta(days=tariff.duration_months * 30)

            # 3) Создаём Subscription.
            subscription = Subscription.objects.create(
                student=student,
                teacher=teacher,
                subject=subject,
                tariff=tariff,
                status=Subscription.Status.ACTIVE,
                lessons_per_week=tariff.lessons_per_week,
                lesson_duration_minutes=tariff.lesson_duration_minutes,
                duration_months=tariff.duration_months,
                total_lessons=total_lessons,
                price_total=price_total,
                price_per_lesson=price_per_lesson,
                commission_rate=commission_rate,
                escrow_balance=price_total,
                started_at=now,
                expires_at=expires_at,
                purchase_idempotency_key=idempotency_key,
            )

            # 4) Auto-генерация Booking'ов.
            bookings_created = cls._generate_bookings_for_subscription(subscription)
            if len(bookings_created) < total_lessons:
                # rollback всей транзакции
                raise NotEnoughCapacity(
                    f'Удалось распланировать только {len(bookings_created)}/{total_lessons} уроков '
                    f'в ближайшие {tariff.duration_months * cls.LOOKAHEAD_WEEKS_MULTIPLIER * 4} недели.'
                )

            # 5) Списание с wallet с привязкой к подписке.
            WalletService.debit(
                user=student,
                amount=price_total,
                tx_type=Transaction.Type.PURCHASE,
                idempotency_key=f'sub-purchase:{subscription.id}',
                description=f'Покупка тарифа «{tariff.name or tariff.subject.name}»',
            )
            # Привяжем эту транзакцию к подписке.
            Transaction.objects.filter(
                idempotency_key=f'sub-purchase:{subscription.id}'
            ).update(related_subscription=subscription)

            return subscription

    # ---- ТЗ flow: заявка → одобрение → оплата → бронь --------------------

    @staticmethod
    def standard_tariff_options(teacher, subject) -> list:
        """Стандартные тарифы 1/2/3 урока в неделю, если у учителя нет своих.

        Цена считается от почасовой ставки учителя по этому предмету:
        price_per_month = hourly_rate × (длительность/60) × уроков_в_нед × 4.
        Возвращает список dict'ов (НЕ моделей) — фронт показывает как опции.
        """
        from teachers.models import TeacherSubject
        ts = TeacherSubject.objects.filter(teacher=teacher, subject=subject).first()
        hourly = Decimal(ts.hourly_rate) if ts and ts.hourly_rate else Decimal('50000')
        duration_minutes = 60
        options = []
        for lpw, name in [(1, 'Базовый'), (2, 'Стандарт'), (3, 'Интенсив')]:
            ppm = (hourly * Decimal(duration_minutes) / Decimal(60)
                   * lpw * Tariff.WEEKS_PER_MONTH).quantize(Decimal('1'))
            options.append({
                'name': name,
                'lessons_per_week': lpw,
                'lesson_duration_minutes': duration_minutes,
                'duration_months': 1,
                'price_per_month': ppm,
                'total_price': ppm,
                'total_lessons': lpw * Tariff.WEEKS_PER_MONTH,
                'is_standard': True,
            })
        return options

    @classmethod
    def create_request(
        cls, *, student, teacher, subject,
        lessons_per_week, lesson_duration_minutes, duration_months, price_per_month,
        tariff=None, preferred_schedule='', idempotency_key,
    ) -> Subscription:
        """Шаг 3 ТЗ: ученик отправляет заявку на обучение (БЕЗ оплаты).

        Создаёт Subscription в статусе PENDING_APPROVAL. Деньги не списываются,
        уроки не бронируются — всё это после одобрения учителем и оплаты.
        """
        if not idempotency_key:
            raise ValueError('idempotency_key is required')

        existing = Subscription.objects.filter(
            purchase_idempotency_key=idempotency_key,
        ).first()
        if existing is not None:
            return existing

        price_per_month = Decimal(price_per_month)
        # Защита от мусорных параметров (прямой вызов мимо формы): total_lessons
        # обязан быть > 0 (иначе нарушится CheckConstraint подписки).
        if lessons_per_week < 1 or duration_months < 1 or price_per_month <= 0:
            raise ValueError('Некорректные параметры тарифа.')
        total_lessons = lessons_per_week * Tariff.WEEKS_PER_MONTH * duration_months
        price_total = (price_per_month * duration_months).quantize(Decimal('0.01'))
        price_per_lesson = (
            (price_total / total_lessons).quantize(Decimal('0.01'))
            if total_lessons else Decimal('0.00')
        )
        commission_rate = Decimal(settings.PLATFORM_COMMISSION_RATE)

        with transaction.atomic():
            # Сериализация по кошельку ученика (как в purchase) + защита от дублей.
            Wallet.objects.select_for_update().get(user=student)
            already = Subscription.objects.filter(
                student=student, teacher=teacher, subject=subject,
                status__in=Subscription.ACTIVE_STATUSES,
            ).exists()
            if already:
                raise AlreadySubscribed(
                    'У вас уже есть заявка или активная подписка к этому учителю по этому предмету.'
                )
            sub = Subscription.objects.create(
                student=student, teacher=teacher, subject=subject, tariff=tariff,
                status=Subscription.Status.PENDING_APPROVAL,
                lessons_per_week=lessons_per_week,
                lesson_duration_minutes=lesson_duration_minutes,
                duration_months=duration_months,
                total_lessons=total_lessons,
                price_total=price_total,
                price_per_lesson=price_per_lesson,
                commission_rate=commission_rate,
                escrow_balance=Decimal('0.00'),
                preferred_schedule=(preferred_schedule or '')[:2000],
                purchase_idempotency_key=idempotency_key,
            )

        cls._notify(
            teacher.user, 'Новая заявка на обучение',
            f'{student.get_full_name() or student.username} хочет заниматься: '
            f'{subject.name}, {lessons_per_week} урок(а)/нед.',
            _safe_reverse('teacher_learning_requests'),
        )
        return sub

    @classmethod
    def approve_request(cls, subscription) -> Subscription:
        """Шаг 4 ТЗ: учитель подтверждает заявку → ученик может оплатить."""
        with transaction.atomic():
            sub = (Subscription.objects.select_for_update()
                   .select_related('student', 'subject').get(pk=subscription.pk))
            if sub.status != Subscription.Status.PENDING_APPROVAL:
                raise ValueError(
                    f'Заявку нельзя подтвердить в статусе «{sub.get_status_display()}».'
                )
            now = timezone.now()
            sub.status = Subscription.Status.PENDING_PAYMENT
            sub.approved_at = now
            sub.approval_expires_at = now + timedelta(hours=cls.APPROVAL_PAYMENT_HOURS)
            sub.save(update_fields=['status', 'approved_at', 'approval_expires_at', 'updated_at'])
        cls._notify(
            sub.student, 'Заявка одобрена — оплатите обучение',
            f'Учитель подтвердил вашу заявку ({sub.subject.name}). '
            f'Оплатите тариф в течение {cls.APPROVAL_PAYMENT_HOURS} ч, чтобы начать.',
            _safe_reverse('subscription_pay', args=[sub.id]),
        )
        return sub

    @classmethod
    def reject_request(cls, subscription, *, reason: str = '') -> Subscription:
        """Шаг 4 ТЗ: учитель отклоняет заявку. Деньги не затрагиваются."""
        with transaction.atomic():
            sub = (Subscription.objects.select_for_update()
                   .select_related('student', 'subject').get(pk=subscription.pk))
            if sub.status != Subscription.Status.PENDING_APPROVAL:
                raise ValueError(
                    f'Заявку нельзя отклонить в статусе «{sub.get_status_display()}».'
                )
            sub.status = Subscription.Status.CANCELLED_BY_TEACHER
            sub.cancelled_at = timezone.now()
            sub.cancellation_reason = (reason or 'Отклонено учителем')[:500]
            sub.save(update_fields=['status', 'cancelled_at', 'cancellation_reason', 'updated_at'])
        cls._notify(
            sub.student, 'Заявка отклонена',
            f'Учитель отклонил заявку на обучение ({sub.subject.name}). '
            f'Деньги не списаны — вы можете выбрать другого учителя.',
            _safe_reverse('home'),
        )
        return sub

    @classmethod
    def pay(cls, subscription, *, idempotency_key: str = '') -> Subscription:
        """Шаг 5 ТЗ: ученик оплачивает одобренную заявку → подписка ACTIVE.

        Деньги уходят в эскроу; уроки бронируются отдельно (book_schedule).
        """
        # Шаг 0: протухшую заявку помечаем EXPIRED в ОТДЕЛЬНОЙ транзакции —
        # иначе raise внутри основного atomic откатил бы и сам перевод в EXPIRED.
        expired = False
        with transaction.atomic():
            sub = Subscription.objects.select_for_update().get(pk=subscription.pk)
            if sub.status != Subscription.Status.PENDING_PAYMENT:
                raise ValueError(
                    f'Оплата недоступна в статусе «{sub.get_status_display()}».'
                )
            if sub.approval_expires_at and sub.approval_expires_at < timezone.now():
                sub.status = Subscription.Status.EXPIRED
                sub.save(update_fields=['status', 'updated_at'])
                expired = True
        if expired:
            raise ValueError('Срок оплаты заявки истёк. Оформите заявку заново.')

        with transaction.atomic():
            sub = Subscription.objects.select_for_update().select_related('subject').get(pk=subscription.pk)
            if sub.status != Subscription.Status.PENDING_PAYMENT:
                raise ValueError(
                    f'Оплата недоступна в статусе «{sub.get_status_display()}».'
                )
            now = timezone.now()
            wallet = Wallet.objects.select_for_update().get(user=sub.student_id)
            if wallet.balance < sub.price_total:
                raise InsufficientFunds(
                    f'Недостаточно средств: на балансе {wallet.balance}, нужно {sub.price_total}. '
                    f'Пополните кошелёк.'
                )
            sub.status = Subscription.Status.ACTIVE
            sub.started_at = now
            sub.expires_at = now + timedelta(days=sub.duration_months * 30)
            sub.escrow_balance = sub.price_total
            sub.save(update_fields=['status', 'started_at', 'expires_at', 'escrow_balance', 'updated_at'])

            WalletService.debit(
                user=sub.student, amount=sub.price_total,
                tx_type=Transaction.Type.PURCHASE,
                idempotency_key=f'sub-purchase:{sub.id}',
                description=f'Оплата обучения ({sub.subject.name})',
            )
            Transaction.objects.filter(
                idempotency_key=f'sub-purchase:{sub.id}'
            ).update(related_subscription=sub)

        cls._notify(
            sub.student, 'Оплата прошла — выберите расписание',
            f'Обучение активно. Выберите удобные дни и время — '
            f'{sub.lessons_per_week} урок(а) в неделю.',
            _safe_reverse('subscription_schedule', args=[sub.id]),
        )
        return sub

    @classmethod
    def book_schedule(cls, subscription, weekly_pattern: list) -> list:
        """Шаг 6 ТЗ: по выбранному недельному шаблону бронируем все уроки.

        weekly_pattern: [{"day": "monday", "time": "18:00"}, ...]
        Длина шаблона должна совпадать с lessons_per_week.
        """
        from teachers.models import Booking
        with transaction.atomic():
            sub = (Subscription.objects.select_for_update()
                   .select_related('teacher', 'subject').get(pk=subscription.pk))
            if sub.status != Subscription.Status.ACTIVE:
                raise ValueError('Расписание доступно только для активной (оплаченной) подписки.')
            if Booking.objects.filter(subscription=sub).exists():
                raise ValueError('Расписание уже сформировано.')
            if not weekly_pattern or not isinstance(weekly_pattern, list):
                raise ValueError('Не выбран шаблон расписания.')
            if len(weekly_pattern) != sub.lessons_per_week:
                raise ValueError(
                    f'Выберите ровно {sub.lessons_per_week} занятия в неделю '
                    f'(выбрано {len(weekly_pattern)}).'
                )
            # Защита от прямого POST в обход UI: каждое (день, время) должно
            # попадать в рабочие часы учителя и не повторяться.
            cls._validate_pattern_within_schedule(sub, weekly_pattern)
            created = cls._generate_bookings_from_pattern(sub, weekly_pattern)
            sub.weekly_pattern = weekly_pattern
            sub.save(update_fields=['weekly_pattern', 'updated_at'])
        return created

    @classmethod
    def expire_unpaid_approvals(cls) -> int:
        """Celery: одобренные, но неоплаченные в срок заявки → EXPIRED."""
        now = timezone.now()
        stale = list(Subscription.objects.filter(
            status=Subscription.Status.PENDING_PAYMENT,
            approval_expires_at__lt=now,
        ))
        count = 0
        for sub in stale:
            with transaction.atomic():
                locked = Subscription.objects.select_for_update().get(pk=sub.pk)
                if locked.status != Subscription.Status.PENDING_PAYMENT:
                    continue
                if not locked.approval_expires_at or locked.approval_expires_at >= timezone.now():
                    continue
                locked.status = Subscription.Status.EXPIRED
                locked.save(update_fields=['status', 'updated_at'])
                count += 1
            cls._notify(
                sub.student, 'Срок оплаты заявки истёк',
                'Одобренная заявка не была оплачена вовремя и истекла. '
                'При желании оформите заявку заново.',
                _safe_reverse('home'),
            )
        return count

    @classmethod
    def _validate_pattern_within_schedule(cls, subscription, weekly_pattern: list) -> None:
        """Каждое (день, время) шаблона должно попадать в рабочие часы учителя
        с запасом на длительность урока и не дублироваться."""
        intervals = subscription.teacher.get_schedule_intervals()
        dur = subscription.lesson_duration_minutes
        seen = set()
        for entry in weekly_pattern:
            day = (entry.get('day') or '').lower()
            try:
                t = dt_time.fromisoformat(entry.get('time'))
            except (ValueError, TypeError):
                raise ValueError('Некорректное время в расписании.')
            key = (day, t.strftime('%H:%M'))
            if key in seen:
                raise ValueError('В шаблоне есть повторяющийся слот.')
            seen.add(key)
            start_min = t.hour * 60 + t.minute
            end_min = start_min + dur
            fits = False
            for from_str, to_str in intervals.get(day, []):
                try:
                    f = dt_time.fromisoformat(from_str)
                    to = dt_time.fromisoformat(to_str)
                except (ValueError, TypeError):
                    continue
                if start_min >= f.hour * 60 + f.minute and end_min <= to.hour * 60 + to.minute:
                    fits = True
                    break
            if not fits:
                raise ValueError('Выбранное время вне рабочих часов учителя.')

    @classmethod
    def _generate_bookings_from_pattern(cls, subscription, weekly_pattern: list,
                                        count: int = None) -> list:
        """Бронирует уроки по недельному шаблону (day/time).

        Идёт по неделям вперёд; для каждого вхождения шаблона создаёт/занимает
        TimeSlot и Booking(confirmed). Занятые слоты пропускает (ищет на след.
        неделе). Останавливается, когда набрал нужное число уроков (count или
        total_lessons) или вышел лимит.
        """
        from teachers.models import Booking, TimeSlot

        teacher = subscription.teacher
        student = subscription.student
        subject = subscription.subject
        duration = timedelta(minutes=subscription.lesson_duration_minutes)
        needed = count if count is not None else subscription.total_lessons
        tz = timezone.get_current_timezone()
        now = timezone.now()

        day_index = {k: i for i, k in enumerate(_WEEKDAY_KEYS)}
        plan = []
        for entry in weekly_pattern:
            d = day_index.get((entry.get('day') or '').lower())
            try:
                t = dt_time.fromisoformat(entry.get('time'))
            except (ValueError, TypeError):
                t = None
            if d is not None and t is not None:
                plan.append((d, t))
        if not plan:
            return []

        created = []
        start_date = (now + timedelta(days=1)).date()
        end_date = start_date + timedelta(
            weeks=subscription.duration_months * 4 * cls.LOOKAHEAD_WEEKS_MULTIPLIER
        )
        cursor = start_date
        while cursor < end_date and len(created) < needed:
            wd = cursor.weekday()
            for (d, t) in plan:
                if d != wd or len(created) >= needed:
                    continue
                start_dt = timezone.make_aware(datetime.combine(cursor, t), tz)
                slot_end = start_dt + duration
                if start_dt < now:
                    continue
                slot = TimeSlot.objects.filter(
                    teacher=teacher, start_at=start_dt, end_at=slot_end,
                ).first()
                if slot is None:
                    slot = TimeSlot.objects.create(
                        teacher=teacher, start_at=start_dt, end_at=slot_end, status='free',
                    )
                elif slot.status != 'free':
                    continue
                slot.status = 'booked'
                slot.save(update_fields=['status', 'updated_at'])
                booking = Booking.objects.create(
                    slot=slot, student=student, subject=subject,
                    status='confirmed', is_trial=False, subscription=subscription,
                )
                if not (booking.meeting_url or '').strip():
                    booking.meeting_url = booking.build_meeting_url()
                    booking.save(update_fields=['meeting_url', 'updated_at'])
                created.append(booking)
            cursor += timedelta(days=1)
        return created

    @staticmethod
    def _notify(user, title: str, text: str, url: str = '') -> None:
        """Лёгкое in-app уведомление (не критично — заворачиваем в try)."""
        try:
            from teachers.models import Notification
            Notification.objects.create(
                title=title[:200], short_text=text[:300], full_text=text,
                target='specific_user', target_user=user,
                action_url=url or '', priority=5, is_active=True,
            )
        except Exception:
            logger.warning('SubscriptionService._notify failed', exc_info=True)

    # ---- internal helpers ----

    # ---- cancellation / refund -------------------------------------------

    # Какие статусы считаем «активными» — только их можно отменить.
    CANCELLABLE_STATUSES = (
        Subscription.Status.PENDING_APPROVAL,
        Subscription.Status.ACTIVE,
        Subscription.Status.PAUSED,
        Subscription.Status.PENDING_PAYMENT,
    )

    # Сколько часов даётся ученику на оплату ПОСЛЕ одобрения заявки учителем.
    APPROVAL_PAYMENT_HOURS = 72

    @classmethod
    def cancel(
        cls,
        subscription: Subscription,
        *,
        cancelled_by: str,
        reason: str = '',
    ) -> dict:
        """Отменить подписку.

        cancelled_by: 'student' | 'teacher' | 'admin'.

        Шаги (атомарно):
          1. Лок подписки.
          2. Для каждого Booking с status='completed' и без payout — release_lesson_payout
             (учитель уже отработал — деньги принадлежат ему).
          3. Будущие confirmed bookings → cancelled_by_*; их слоты → 'free'.
          4. Refund остаточного escrow на wallet ученика (Transaction.Type.REFUND).
          5. subscription.status / cancelled_at / cancellation_reason / escrow_balance = 0.

        Возвращает: {'refunded': X, 'paid_out': N, 'cancelled_bookings': M}.
        Идемпотентность: повторный вызов на уже cancelled — CancellationError.
        """
        from teachers.models import Booking

        status_map = {
            'student': Subscription.Status.CANCELLED_BY_STUDENT,
            'teacher': Subscription.Status.CANCELLED_BY_TEACHER,
            'admin':   Subscription.Status.CANCELLED_BY_ADMIN,
        }
        if cancelled_by not in status_map:
            raise ValueError(f'invalid cancelled_by={cancelled_by!r}')

        booking_status_map = {
            'student': 'cancelled_by_student',
            'teacher': 'cancelled_by_teacher',
            'admin':   'cancelled_by_teacher',  # для booking нет admin-варианта
        }

        with transaction.atomic():
            sub = (
                Subscription.objects
                .select_for_update()
                .select_related('teacher', 'student')
                .get(pk=subscription.pk)
            )

            if sub.status not in cls.CANCELLABLE_STATUSES:
                raise CancellationError(
                    f'Нельзя отменить подписку в статусе «{sub.get_status_display()}».'
                )

            # 1) Доплатить учителю за доставленные-но-не-paid уроки
            #    (completed ИЛИ no_show_student — учитель отработал).
            unpaid_completed = list(
                Booking.objects
                .filter(subscription=sub, status__in=('completed', 'no_show_student'))
                .exclude(
                    id__in=Transaction.objects.filter(
                        related_subscription=sub,
                        type=Transaction.Type.LESSON_PAYOUT,
                    ).values_list('related_booking_id', flat=True)
                )
                .select_related('slot')
            )
            paid_now = 0
            for booking in unpaid_completed:
                if cls.release_lesson_payout(booking):
                    paid_now += 1

            # release_lesson_payout мог изменить sub — refresh.
            sub.refresh_from_db()

            # 2) Отменить будущие confirmed bookings + освободить слоты.
            now = timezone.now()

            # 1.5) Прошедшие, но ещё не завершённые уроки (slot закончился, а
            # mark_completed_lessons не успел) доурегулируем как обычно: учитель
            # был → completed + выплата; не был → no_show (деньги вернутся ниже
            # в остатке эскроу). Иначе их стоимость молча уходила бы ученику.
            past_unsettled = list(
                Booking.objects
                .filter(subscription=sub, status='confirmed', slot__end_at__lte=now)
                .select_related('slot')
            )
            for b in past_unsettled:
                try:
                    if b.settle_after_end() in ('completed', 'no_show_student'):
                        cls.release_lesson_payout(b)
                except PayoutError:
                    pass
                except Exception:
                    logger.warning('cancel(): settle past lesson %s failed', b.pk, exc_info=True)
            if past_unsettled:
                sub.refresh_from_db()  # escrow/lessons_paid_out изменились

            future = list(
                Booking.objects
                .filter(subscription=sub, status__in=('confirmed', 'pending'),
                        slot__end_at__gt=now)
                .select_related('slot')
            )
            cancel_status = booking_status_map[cancelled_by]
            cancelled_count = 0
            for b in future:
                b.status = cancel_status
                b.save(update_fields=['status', 'updated_at'])
                # Освобождаем слот, если он держался под эту бронь.
                if b.slot and b.slot.status in ('booked', 'held'):
                    b.slot.status = 'free'
                    b.slot.hold_expires_at = None
                    b.slot.save(update_fields=['status', 'hold_expires_at', 'updated_at'])
                cancelled_count += 1

            # 3) Refund остатка эскроу ученику.
            refunded = Decimal('0.00')
            if sub.escrow_balance > 0:
                WalletService.credit(
                    user=sub.student,
                    amount=sub.escrow_balance,
                    tx_type=Transaction.Type.REFUND,
                    idempotency_key=f'sub-refund:{sub.id}',
                    description=f'Возврат за отменённую подписку (остаток эскроу). Причина: {reason}',
                )
                # Привязка к подписке.
                Transaction.objects.filter(
                    idempotency_key=f'sub-refund:{sub.id}'
                ).update(related_subscription=sub)
                refunded = sub.escrow_balance
                sub.escrow_balance = Decimal('0.00')

            # 4) Финализируем подписку.
            sub.status = status_map[cancelled_by]
            sub.cancelled_at = now
            sub.cancellation_reason = (reason or '').strip()[:1000]
            sub.save(update_fields=[
                'status', 'escrow_balance', 'cancelled_at', 'cancellation_reason',
                'updated_at',
            ])

            return {
                'refunded': refunded,
                'paid_out': paid_now,
                'cancelled_bookings': cancelled_count,
            }

    @classmethod
    def settle_expired(cls, subscription: Subscription) -> Optional[dict]:
        """Закрыть истёкшую активную подписку и слить зависший escrow.

        Срабатывает для ACTIVE/PAUSED подписок, у которых вышел срок
        (`expires_at < now − PAYOUT_GRACE_HOURS`), но уроки так и не были
        проведены (бесконечные переносы/неявки). Без этого escrow висел бы
        вечно — деньги «ничьи».

        Шаги (атомарно):
          1. Доурегулировать прошедшие confirmed-уроки (settle_after_end + payout).
          2. Отменить будущие confirmed/pending брони, освободить слоты.
          3. Вернуть остаток escrow ученику (ключ `sub-expire:{id}`).
          4. Финальный статус: COMPLETED если все уроки доставлены, иначе EXPIRED.

        Идемпотентно: повторный вызов на завершённой подписке вернёт None.
        Возвращает {'refunded', 'paid_out', 'cancelled_bookings'} либо None.
        """
        from teachers.models import Booking

        grace = timedelta(hours=settings.PAYOUT_GRACE_HOURS)
        eligible = (Subscription.Status.ACTIVE, Subscription.Status.PAUSED)

        with transaction.atomic():
            sub = (
                Subscription.objects
                .select_for_update()
                .select_related('teacher', 'student')
                .get(pk=subscription.pk)
            )

            now = timezone.now()
            if sub.status not in eligible:
                return None
            if not sub.expires_at or sub.expires_at > now - grace:
                return None  # ещё не истекла (с учётом grace)

            paid_now = 0

            # 1) Доплатить учителю за completed-но-не-paid уроки (он отработал).
            unpaid_completed = list(
                Booking.objects
                .filter(subscription=sub, status__in=('completed', 'no_show_student'))
                .exclude(
                    id__in=Transaction.objects.filter(
                        related_subscription=sub,
                        type=Transaction.Type.LESSON_PAYOUT,
                    ).values_list('related_booking_id', flat=True)
                )
                .select_related('slot')
            )
            for b in unpaid_completed:
                try:
                    if cls.release_lesson_payout(b):
                        paid_now += 1
                except PayoutError:
                    pass
            if unpaid_completed:
                sub.refresh_from_db()

            # 2) Доурегулировать прошедшие, но незакрытые (confirmed) уроки.
            past_unsettled = list(
                Booking.objects
                .filter(subscription=sub, status='confirmed', slot__end_at__lte=now)
                .select_related('slot')
            )
            for b in past_unsettled:
                try:
                    result = b.settle_after_end()
                    if result in ('completed', 'no_show_student'):
                        if cls.release_lesson_payout(b):
                            paid_now += 1
                except PayoutError:
                    pass
                except Exception:
                    logger.warning('settle_expired: settle %s failed', b.pk, exc_info=True)
            if past_unsettled:
                sub.refresh_from_db()

            # 3) Отменить будущие брони, освободить слоты.
            future = list(
                Booking.objects
                .filter(subscription=sub, status__in=('confirmed', 'pending'),
                        slot__end_at__gt=now)
                .select_related('slot')
            )
            cancelled_count = 0
            for b in future:
                b.status = 'expired'
                b.expires_at = None
                b.save(update_fields=['status', 'expires_at', 'updated_at'])
                if b.slot and b.slot.status in ('booked', 'held'):
                    b.slot.status = 'free'
                    b.slot.hold_expires_at = None
                    b.slot.save(update_fields=['status', 'hold_expires_at', 'updated_at'])
                cancelled_count += 1

            # 4) Вернуть остаток escrow ученику.
            refunded = Decimal('0.00')
            if sub.escrow_balance > 0:
                WalletService.credit(
                    user=sub.student,
                    amount=sub.escrow_balance,
                    tx_type=Transaction.Type.REFUND,
                    idempotency_key=f'sub-expire:{sub.id}',
                    description='Возврат за недоставленные уроки истёкшей подписки.',
                )
                Transaction.objects.filter(
                    idempotency_key=f'sub-expire:{sub.id}'
                ).update(related_subscription=sub)
                refunded = sub.escrow_balance
                sub.escrow_balance = Decimal('0.00')

            # 5) Финальный статус.
            if sub.lessons_paid_out >= sub.total_lessons:
                sub.status = Subscription.Status.COMPLETED
            else:
                sub.status = Subscription.Status.EXPIRED
            sub.save(update_fields=['status', 'escrow_balance', 'updated_at'])

        try:
            cls._notify(
                sub.student,
                'Подписка завершена',
                f'Срок подписки с {sub.teacher} истёк. '
                + (f'Возвращено {refunded} на баланс.' if refunded else 'Все уроки проведены.'),
            )
        except Exception:
            logger.warning('settle_expired: notify failed for sub %s', sub.pk, exc_info=True)

        return {
            'refunded': refunded,
            'paid_out': paid_now,
            'cancelled_bookings': cancelled_count,
        }

    # ---- pause / resume (v2 Шаг 6) ---------------------------------------

    @classmethod
    def pause(cls, subscription: Subscription, *, reason: str = '') -> int:
        """Приостановить активную подписку.

        Деньги (escrow) остаются за подпиской — возврата нет. Будущие брони
        снимаются и слоты освобождаются (календарь учителя на паузе свободен).
        Прошедшие уроки не трогаем. Возвращает число снятых будущих броней.
        """
        from teachers.models import Booking
        with transaction.atomic():
            sub = Subscription.objects.select_for_update().get(pk=subscription.pk)
            if sub.status != Subscription.Status.ACTIVE:
                raise CancellationError(
                    f'Приостановить можно только активную подписку '
                    f'(сейчас «{sub.get_status_display()}»).'
                )
            now = timezone.now()
            future = list(
                Booking.objects
                .filter(subscription=sub, status__in=('confirmed', 'pending'),
                        slot__start_at__gt=now)
                .select_related('slot')
            )
            freed = 0
            for b in future:
                b.status = 'expired'
                b.expires_at = None
                b.save(update_fields=['status', 'expires_at', 'updated_at'])
                if b.slot and b.slot.status in ('booked', 'held'):
                    b.slot.status = 'free'
                    b.slot.hold_expires_at = None
                    b.slot.save(update_fields=['status', 'hold_expires_at', 'updated_at'])
                freed += 1
            sub.status = Subscription.Status.PAUSED
            sub.paused_at = now
            if reason:
                sub.cancellation_reason = (reason or '').strip()[:1000]
            sub.save(update_fields=['status', 'paused_at', 'cancellation_reason', 'updated_at'])

        try:
            cls._notify(sub.student, 'Подписка приостановлена',
                        f'Подписка с {sub.teacher} на паузе. Возобновите в любой момент.')
        except Exception:
            logger.warning('pause notify failed sub=%s', sub.pk, exc_info=True)
        return freed

    @classmethod
    def resume(cls, subscription: Subscription) -> int:
        """Возобновить приостановленную подписку.

        Срок (expires_at) сдвигается на длительность паузы, расписание
        перегенерируется на оставшиеся уроки (total_lessons − completed_lessons).
        Возвращает число вновь созданных броней.
        """
        with transaction.atomic():
            sub = (Subscription.objects.select_for_update()
                   .select_related('teacher', 'student', 'subject').get(pk=subscription.pk))
            if sub.status != Subscription.Status.PAUSED:
                raise CancellationError(
                    f'Возобновить можно только приостановленную подписку '
                    f'(сейчас «{sub.get_status_display()}»).'
                )
            now = timezone.now()
            # Сдвигаем срок на длительность паузы.
            if sub.paused_at and sub.expires_at:
                sub.expires_at = sub.expires_at + (now - sub.paused_at)
            sub.status = Subscription.Status.ACTIVE
            sub.paused_at = None
            sub.save(update_fields=['status', 'paused_at', 'expires_at', 'updated_at'])

            remaining = max(0, sub.total_lessons - sub.completed_lessons)
            created = []
            if remaining > 0:
                if sub.weekly_pattern:
                    created = cls._generate_bookings_from_pattern(
                        sub, sub.weekly_pattern, count=remaining)
                else:
                    created = cls._generate_bookings_for_subscription(sub, count=remaining)

        try:
            cls._notify(sub.student, 'Подписка возобновлена',
                        f'Подписка с {sub.teacher} снова активна. '
                        f'Запланировано уроков: {len(created)}.')
        except Exception:
            logger.warning('resume notify failed sub=%s', sub.pk, exc_info=True)
        return len(created)

    # ---- payout / completion --------------------------------------------

    @classmethod
    def release_lesson_payout(cls, booking, *, allow_late_cancel: bool = False) -> bool:
        """Начислить деньги учителю и комиссию платформе за один проведённый урок.

        Атомарно:
          - lock Subscription (select_for_update)
          - проверка no-double-payout (UNIQUE idempotency_key)
          - escrow_balance -= price_per_lesson
          - teacher.wallet  += (1 - commission_rate) * price_per_lesson
          - platform.wallet += commission_rate * price_per_lesson
          - lessons_paid_out += 1
          - если lessons_paid_out == total_lessons → status='completed'

        allow_late_cancel: если True, допускает выплату по уроку, который ученик
        отменил слишком поздно (cancelled_by_student) — штраф за позднюю отмену
        уходит учителю (v2 Шаг 5).

        Возвращает True если payout произошёл, False если уже был сделан раньше.
        """
        from .platform_account import get_or_create_platform_user

        if booking.subscription_id is None:
            raise PayoutError('booking без subscription — нечего выплачивать')
        # Доставленный урок = учитель отработал: completed ИЛИ ученик не пришёл
        # (no_show_student); либо поздняя отмена ученика (штраф учителю).
        allowed_statuses = ['completed', 'no_show_student']
        if allow_late_cancel:
            allowed_statuses.append('cancelled_by_student')
        if booking.status not in allowed_statuses:
            raise PayoutError(
                f'booking.status={booking.status}, ожидался {"/".join(allowed_statuses)}'
            )
        # Заморозка выплаты на время открытого спора (ТЗ шаг 8).
        if LessonDispute.objects.filter(
            booking_id=booking.id, status=LessonDispute.Status.OPEN,
        ).exists():
            raise PayoutError('по уроку открыт спор — выплата заморожена')
        # Если за урок уже сделан возврат ученику (спор решён в его пользу /
        # отмена) — платить учителю НЕЛЬЗЯ, иначе урок «оплачивается» дважды.
        if Transaction.objects.filter(idempotency_key=f'lesson-refund:{booking.id}').exists():
            return False

        payout_key = f'lesson-payout:{booking.id}'
        commission_key = f'commission:{booking.id}'
        # Быстрый путь: payout И комиссия уже есть → делать нечего.
        if (Transaction.objects.filter(idempotency_key=payout_key).exists()
                and Transaction.objects.filter(idempotency_key=commission_key).exists()):
            return False

        with transaction.atomic():
            sub = (
                Subscription.objects
                .select_for_update()
                .select_related('teacher__user')
                .get(pk=booking.subscription_id)
            )

            # Уже ли эскроу был списан под этот урок (т.е. payout учителю выполнен).
            # Если да — escrow трогать нельзя, но комиссию (если её нет) надо добить:
            # это закрывает дыру "teacher credit прошёл, commission credit упал".
            already_paid = Transaction.objects.filter(idempotency_key=payout_key).exists()

            # На последнем уроке сливаем весь остаток эскроу (включая "пыль" от
            # округления price_total/total_lessons), иначе клампим к price_per_lesson.
            is_last = (sub.lessons_paid_out + 1) >= sub.total_lessons
            if already_paid:
                pay_base = sub.price_per_lesson  # для пересчёта commission/teacher_amount
            elif is_last:
                pay_base = sub.escrow_balance
            else:
                pay_base = min(sub.price_per_lesson, sub.escrow_balance)

            if not already_paid and pay_base <= 0:
                raise PayoutError(
                    f'нет эскроу для выплаты: escrow={sub.escrow_balance}'
                )

            commission = (pay_base * sub.commission_rate).quantize(Decimal('0.01'))
            teacher_amount = (pay_base - commission).quantize(Decimal('0.01'))

            teacher_user = sub.teacher.user
            platform_user = get_or_create_platform_user()

            # 1) Зачисление учителю (идемпотентно по ключу).
            WalletService.credit(
                user=teacher_user,
                amount=teacher_amount,
                tx_type=Transaction.Type.LESSON_PAYOUT,
                idempotency_key=payout_key,
                description=f'Выплата за урок {booking.id}',
                related_booking=booking,
            )

            # 2) Зачисление комиссии платформе (идемпотентно — добьёт, если упало раньше).
            WalletService.credit(
                user=platform_user,
                amount=commission,
                tx_type=Transaction.Type.COMMISSION,
                idempotency_key=commission_key,
                description=f'Комиссия с урока {booking.id}',
                related_booking=booking,
            )

            # 3) Привязка к подписке.
            Transaction.objects.filter(
                idempotency_key__in=[payout_key, commission_key]
            ).update(related_subscription=sub)

            # 4) Уменьшаем эскроу и счётчики подписки — ТОЛЬКО если это новый payout.
            if not already_paid:
                sub.escrow_balance = sub.escrow_balance - pay_base
                if sub.escrow_balance < 0:
                    sub.escrow_balance = Decimal('0.00')
                sub.lessons_paid_out = sub.lessons_paid_out + 1
                if sub.lessons_paid_out >= sub.total_lessons:
                    sub.status = Subscription.Status.COMPLETED
                sub.save(update_fields=['escrow_balance', 'lessons_paid_out', 'status', 'updated_at'])

            return not already_paid

    @classmethod
    def refund_lesson(cls, booking, *, cancelled_by: str = 'teacher', reason: str = '') -> Decimal:
        """Вернуть ученику стоимость ОДНОГО отменённого урока подписки.

        Закрывает баг «зависшего эскроу»: при отмене отдельного урока деньги за
        него раньше навсегда оставались в escrow, а подписка не могла дойти до
        completed (completed_lessons/lessons_paid_out никогда не догоняли
        total_lessons). Теперь стоимость урока возвращается на кошелёк ученика,
        а пакет уменьшается на 1 урок — инвариант снова сходится.

        Если это был последний урок пакета — делегируем полной отмене подписки
        (cls.cancel), т.к. total_lessons не может стать 0 (CheckConstraint).

        Идемпотентно по key='lesson-refund:<booking.id>'. Возвращает сумму
        возврата (0, если нечего/уже возвращено/уже выплачено учителю).
        """
        if booking.subscription_id is None:
            return Decimal('0.00')

        refund_key = f'lesson-refund:{booking.id}'
        payout_key = f'lesson-payout:{booking.id}'
        if Transaction.objects.filter(idempotency_key=refund_key).exists():
            return Decimal('0.00')
        # За проведённый-и-оплаченный урок возврата быть не может.
        if Transaction.objects.filter(idempotency_key=payout_key).exists():
            return Decimal('0.00')

        with transaction.atomic():
            sub = (
                Subscription.objects
                .select_for_update()
                .select_related('student')
                .get(pk=booking.subscription_id)
            )
            # Повторная проверка под локом (закрывает гонку с release_lesson_payout).
            if Transaction.objects.filter(idempotency_key=refund_key).exists():
                return Decimal('0.00')
            if Transaction.objects.filter(idempotency_key=payout_key).exists():
                return Decimal('0.00')

            # Последний урок пакета — нельзя уменьшить total_lessons до 0,
            # поэтому отменяем подписку целиком (вернёт весь остаток эскроу).
            if sub.total_lessons <= 1 and sub.status in cls.CANCELLABLE_STATUSES:
                result = cls.cancel(sub, cancelled_by=cancelled_by, reason=reason or 'Отмена урока')
                return Decimal(result.get('refunded', Decimal('0.00')))

            amount = min(sub.price_per_lesson, sub.escrow_balance).quantize(Decimal('0.01'))
            if amount <= 0:
                return Decimal('0.00')

            sub.escrow_balance = sub.escrow_balance - amount
            sub.total_lessons = sub.total_lessons - 1
            sub.save(update_fields=['escrow_balance', 'total_lessons', 'updated_at'])

            WalletService.credit(
                user=sub.student,
                amount=amount,
                tx_type=Transaction.Type.REFUND,
                idempotency_key=refund_key,
                description=f'Возврат за отменённый урок подписки. {reason}'[:300],
                related_booking=booking,
            )
            Transaction.objects.filter(idempotency_key=refund_key).update(related_subscription=sub)

            # Если после уменьшения пакета все оставшиеся уроки уже выплачены —
            # подписка фактически завершена.
            if sub.status == Subscription.Status.ACTIVE and sub.lessons_paid_out >= sub.total_lessons:
                sub.status = Subscription.Status.COMPLETED
                sub.save(update_fields=['status', 'updated_at'])

            return amount

    @classmethod
    def cancel_lesson(cls, booking, *, cancelled_by: str, reason: str = '') -> dict:
        """Политика отмены ОДНОГО урока подписки (v2 Шаг 5).

        Правила:
          * учитель отменяет → всегда полный возврат ученику (вина учителя);
          * ученик отменяет заблаговременно (> CANCELLATION_FULL_REFUND_HOURS до
            начала) → полный возврат, урок возвращается в квоту;
          * ученик отменяет поздно (≤ порога) → урок списывается, штраф уходит
            учителю (release_lesson_payout с allow_late_cancel).

        Возвращает {'refunded': Decimal, 'charged': bool, 'policy': str}.
        Предполагается, что статус booking уже переведён в cancelled_by_* вызовом
        Booking.cancel_by_student/cancel_by_teacher.
        """
        if booking.subscription_id is None:
            return {'refunded': Decimal('0.00'), 'charged': False, 'policy': 'not_subscription'}

        # Учитель отменил — полный возврат независимо от времени.
        if cancelled_by != 'student':
            refunded = cls.refund_lesson(booking, cancelled_by=cancelled_by, reason=reason)
            return {'refunded': refunded, 'charged': False, 'policy': 'teacher_full_refund'}

        # Ученик: смотрим, насколько заблаговременно отменил.
        threshold = timedelta(hours=settings.CANCELLATION_FULL_REFUND_HOURS)
        lead = booking.slot.start_at - timezone.now()
        if lead >= threshold:
            refunded = cls.refund_lesson(booking, cancelled_by='student', reason=reason)
            return {'refunded': refunded, 'charged': False, 'policy': 'student_full_refund'}

        # Поздняя отмена — урок засчитывается учителю (без возврата ученику).
        try:
            cls.release_lesson_payout(booking, allow_late_cancel=True)
        except PayoutError:
            # Например, уже выплачен/возвращён — деньги тронуты не будут.
            pass
        return {'refunded': Decimal('0.00'), 'charged': True, 'policy': 'student_late_charge'}

    @classmethod
    def _generate_bookings_for_subscription(cls, subscription: Subscription,
                                            count: int = None) -> list:
        """Заполняет slots/bookings для подписки из teacher.weekly_schedule.

        Алгоритм:
          - Идём по дням, начиная с завтрашнего, на LOOKAHEAD недель.
          - Для каждого дня берём интервалы из weekly_schedule и режем на куски
            lesson_duration_minutes.
          - Для каждого получившегося куска: либо переиспользуем существующий
            TimeSlot(status=free), либо создаём новый TimeSlot.
          - Помечаем его как 'booked' и создаём Booking(status='confirmed').
          - Останавливаемся, когда набрали нужное число (count или total_lessons).
        """
        from teachers.models import Booking, TimeSlot

        teacher = subscription.teacher
        student = subscription.student
        subject = subscription.subject
        duration = timedelta(minutes=subscription.lesson_duration_minutes)
        needed = count if count is not None else subscription.total_lessons

        schedule = teacher.get_schedule_intervals()
        if not any(schedule.values()):
            return []

        tz = timezone.get_current_timezone()
        now = timezone.now()
        cursor_date = (now + timedelta(days=1)).date()
        end_date = cursor_date + timedelta(
            weeks=subscription.duration_months * 4 * cls.LOOKAHEAD_WEEKS_MULTIPLIER
        )

        created_bookings = []

        while cursor_date < end_date and len(created_bookings) < needed:
            day_key = _WEEKDAY_KEYS[cursor_date.weekday()]
            intervals = schedule.get(day_key, [])
            for from_str, to_str in intervals:
                if len(created_bookings) >= needed:
                    break
                try:
                    from_t = dt_time.fromisoformat(from_str)
                    to_t = dt_time.fromisoformat(to_str)
                except (ValueError, TypeError):
                    continue
                start_dt = timezone.make_aware(datetime.combine(cursor_date, from_t), tz)
                end_dt_window = timezone.make_aware(datetime.combine(cursor_date, to_t), tz)

                cursor_dt = start_dt
                while cursor_dt + duration <= end_dt_window and len(created_bookings) < needed:
                    slot_end = cursor_dt + duration
                    if cursor_dt < now:
                        cursor_dt = slot_end
                        continue

                    # Ищем существующий free слот ровно на этот интервал.
                    slot = TimeSlot.objects.filter(
                        teacher=teacher,
                        start_at=cursor_dt,
                        end_at=slot_end,
                    ).first()

                    if slot is None:
                        # Создаём новый
                        slot = TimeSlot.objects.create(
                            teacher=teacher,
                            start_at=cursor_dt,
                            end_at=slot_end,
                            status='free',
                        )
                    elif slot.status != 'free':
                        # Слот уже занят (другой ученик или blocked) — пропускаем.
                        cursor_dt = slot_end
                        continue

                    slot.status = 'booked'
                    slot.save(update_fields=['status', 'updated_at'])

                    booking = Booking.objects.create(
                        slot=slot,
                        student=student,
                        subject=subject,
                        status='confirmed',
                        is_trial=False,
                        subscription=subscription,
                    )
                    created_bookings.append(booking)
                    cursor_dt = slot_end

            cursor_date += timedelta(days=1)

        return created_bookings


# ---------- WithdrawalService ---------------------------------------------


class WithdrawalService:
    """Заявки на вывод средств учителя.

    Money flow:
      create_request: wallet -= amount (WITHDRAWAL), создаётся pending-заявка
      approve:        статус → approved (деньги уже списаны, перевод идёт)
      complete:       статус → completed (админ подтвердил перевод)
      reject:         wallet += amount (REFUND), статус → rejected
      cancel_by_user: wallet += amount (REFUND), статус → cancelled
                      разрешено только пока status=pending.
    """

    @classmethod
    def create_request(
        cls,
        *,
        user,
        amount: Decimal,
        payout_method: str,
        payout_details: str,
        comment: str = '',
        idempotency_key: str,
    ) -> WithdrawalRequest:
        if not idempotency_key:
            raise ValueError('idempotency_key is required')

        amount = Decimal(amount)
        min_amt = Decimal(settings.MIN_WITHDRAWAL_AMOUNT)
        if amount < min_amt:
            raise WithdrawalAmountError(
                f'Минимальная сумма вывода — {int(min_amt)} сум.'
            )

        existing = WithdrawalRequest.objects.filter(idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing

        if not payout_details or not payout_details.strip():
            raise WithdrawalError('Укажите реквизиты для вывода.')

        with transaction.atomic():
            WalletService.debit(
                user=user,
                amount=amount,
                tx_type=Transaction.Type.WITHDRAWAL,
                idempotency_key=f'withdrawal-debit:{idempotency_key}',
                description=f'Заявка на вывод {amount} сум',
            )
            wr = WithdrawalRequest.objects.create(
                user=user,
                amount=amount,
                payout_method=payout_method,
                payout_details=payout_details.strip()[:200],
                comment=(comment or '').strip()[:500],
                idempotency_key=idempotency_key,
                status=WithdrawalRequest.Status.PENDING,
            )
            return wr

    @classmethod
    def approve(cls, wr: WithdrawalRequest, *, admin_user, note: str = '') -> WithdrawalRequest:
        with transaction.atomic():
            wr = WithdrawalRequest.objects.select_for_update().get(pk=wr.pk)
            if wr.status != WithdrawalRequest.Status.PENDING:
                raise WithdrawalError(
                    f'Только PENDING заявки можно одобрить (сейчас {wr.status}).'
                )
            wr.status = WithdrawalRequest.Status.APPROVED
            wr.reviewed_by = admin_user
            wr.reviewed_at = timezone.now()
            if note:
                wr.admin_note = note.strip()[:1000]
            wr.save(update_fields=[
                'status', 'reviewed_by', 'reviewed_at', 'admin_note', 'updated_at',
            ])
            return wr

    @classmethod
    def complete(cls, wr: WithdrawalRequest, *, admin_user, note: str = '') -> WithdrawalRequest:
        with transaction.atomic():
            wr = WithdrawalRequest.objects.select_for_update().get(pk=wr.pk)
            if wr.status not in (
                WithdrawalRequest.Status.PENDING, WithdrawalRequest.Status.APPROVED,
            ):
                raise WithdrawalError(
                    f'Завершить можно только pending/approved (сейчас {wr.status}).'
                )
            wr.status = WithdrawalRequest.Status.COMPLETED
            wr.reviewed_by = admin_user
            wr.completed_at = timezone.now()
            if not wr.reviewed_at:
                wr.reviewed_at = wr.completed_at
            if note:
                wr.admin_note = note.strip()[:1000]
            wr.save(update_fields=[
                'status', 'reviewed_by', 'reviewed_at', 'completed_at',
                'admin_note', 'updated_at',
            ])
            return wr

    @classmethod
    def reject(cls, wr: WithdrawalRequest, *, admin_user, note: str) -> WithdrawalRequest:
        if not (note or '').strip():
            raise WithdrawalError('Для отклонения требуется указать причину.')

        with transaction.atomic():
            wr = WithdrawalRequest.objects.select_for_update().get(pk=wr.pk)
            if wr.status not in (
                WithdrawalRequest.Status.PENDING, WithdrawalRequest.Status.APPROVED,
            ):
                raise WithdrawalError(
                    f'Отклонить можно только pending/approved (сейчас {wr.status}).'
                )
            WalletService.credit(
                user=wr.user,
                amount=wr.amount,
                tx_type=Transaction.Type.REFUND,
                idempotency_key=f'withdrawal-reject:{wr.id}',
                description=f'Отклонена заявка на вывод. Причина: {note[:200]}',
            )
            wr.status = WithdrawalRequest.Status.REJECTED
            wr.reviewed_by = admin_user
            wr.reviewed_at = timezone.now()
            wr.admin_note = note.strip()[:1000]
            wr.save(update_fields=[
                'status', 'reviewed_by', 'reviewed_at', 'admin_note', 'updated_at',
            ])
            return wr

    @classmethod
    def cancel_by_user(cls, wr: WithdrawalRequest) -> WithdrawalRequest:
        with transaction.atomic():
            wr = WithdrawalRequest.objects.select_for_update().get(pk=wr.pk)
            if wr.status != WithdrawalRequest.Status.PENDING:
                raise WithdrawalError(
                    f'Отменить можно только PENDING заявку (сейчас {wr.status}).'
                )
            WalletService.credit(
                user=wr.user,
                amount=wr.amount,
                tx_type=Transaction.Type.REFUND,
                idempotency_key=f'withdrawal-cancel:{wr.id}',
                description='Отмена заявки на вывод пользователем',
            )
            wr.status = WithdrawalRequest.Status.CANCELLED
            wr.cancelled_at = timezone.now()
            wr.save(update_fields=['status', 'cancelled_at', 'updated_at'])
            return wr


# ---------- TrialService (Phase 9.5) --------------------------------------


class TrialAlreadyTaken(Exception):
    """У ученика уже есть пробный урок с этим учителем по этому предмету."""


class TrialNotPaid(Exception):
    """Попытка применить платную логику к бесплатному пробному."""


class TrialService:
    """Платный пробный урок: эскроу-логика как у подписки, но на 1 урок.

    Money flow:
      book_paid_trial:    student.wallet -= trial_price; trial_price_paid = X
      release_payout:     teacher.wallet += 85%; platform.wallet += 15%
      refund_trial:       student.wallet += trial_price (RFUND)

    Бесплатный пробный (is_free_trial=True) НЕ ходит через этот сервис —
    создаётся обычным Booking.create_hold без денег.
    """

    @staticmethod
    def _existing_trial_qs(student, teacher, subject=None):
        """Уже существующий пробный у этого ученика с этим УЧИТЕЛЕМ.

        Правило (ТЗ): один пробный на пару (student, teacher) — не на предмет.
        Поэтому `subject` в фильтре НЕ участвует (параметр оставлен в сигнатуре
        для обратной совместимости вызовов).

        «Израсходованным» считаем любой пробный, КРОМЕ не состоявшихся по вине
        учителя/системы (отклонён учителем или истёк без подтверждения) — там
        ученику честно даём попробовать снова. Собственная отмена ученика
        (cancelled_by_student) попытку расходует — иначе тривиальный обход
        «отменил → забронировал заново» давал бы безлимит бесплатных пробных.
        """
        from teachers.models import Booking
        return Booking.objects.filter(
            student=student,
            slot__teacher=teacher,
            is_trial=True,
        ).exclude(status__in=['expired', 'cancelled_by_teacher'])

    @classmethod
    def book_paid_trial(
        cls,
        *,
        student,
        slot_id,
        teacher_subject,
        message: str = '',
        hold_minutes=None,
    ):
        """Атомарно: списать trial_price + Booking.create_hold + привязать Transaction.

        teacher_subject — экземпляр TeacherSubject (с is_free_trial=False, trial_price>0).
        """
        from teachers.models import Booking, TimeSlot

        if teacher_subject.is_free_trial or not teacher_subject.trial_price:
            raise TrialNotPaid('этот пробный бесплатный — используй обычный create_hold')

        trial_price = Decimal(teacher_subject.trial_price)
        teacher = teacher_subject.teacher
        subject = teacher_subject.subject

        with transaction.atomic():
            # 1) Анти-абуз: один пробный на (student, teacher, subject).
            existing = cls._existing_trial_qs(student, teacher).first()
            if existing is not None:
                raise TrialAlreadyTaken(
                    'У вас уже был пробный урок с этим учителем. '
                    'Пробный доступен только один раз — оформите подписку, чтобы продолжить.'
                )

            # 2) Проверка слота (быстрая, до debit'а).
            slot = TimeSlot.objects.select_for_update().get(pk=slot_id)
            if slot.teacher_id != teacher.id:
                raise ValueError('slot не принадлежит этому учителю')
            if slot.status != 'free':
                from teachers.models import SlotUnavailable
                raise SlotUnavailable(f'Слот занят: {slot.status}')

            # 3) Списываем деньги ученика (InsufficientFunds → откатит транзакцию).
            booking = Booking.create_hold(
                slot_id=slot_id,
                student=student,
                subject=subject,
                message=message,
                is_trial=True,
                hold_minutes=hold_minutes,
            )
            booking.trial_price_paid = trial_price
            booking.save(update_fields=['trial_price_paid', 'updated_at'])

            WalletService.debit(
                user=student,
                amount=trial_price,
                tx_type=Transaction.Type.PURCHASE,
                idempotency_key=f'trial-debit:{booking.id}',
                description=f'Оплата пробного урока ({subject.name})',
                related_booking=booking,
            )
            return booking

    @classmethod
    def release_trial_payout(cls, booking) -> bool:
        """После grace window: учителю 85%, платформе 15%. Идемпотентно.

        Возвращает True если выплата произошла, False если уже была выплачена раньше.
        """
        from .platform_account import get_or_create_platform_user

        if not booking.is_trial or not booking.trial_price_paid:
            raise PayoutError('booking не является платным пробным')
        # Доставленный пробный = учитель отработал: completed ИЛИ ученик не пришёл.
        if booking.status not in ('completed', 'no_show_student'):
            raise PayoutError(
                f'booking.status={booking.status}, ожидался completed/no_show_student'
            )
        # Заморозка выплаты на время открытого спора (ТЗ шаг 8).
        if LessonDispute.objects.filter(
            booking_id=booking.id, status=LessonDispute.Status.OPEN,
        ).exists():
            raise PayoutError('по уроку открыт спор — выплата заморожена')

        payout_key = f'trial-payout:{booking.id}'
        if Transaction.objects.filter(idempotency_key=payout_key).exists():
            return False

        with transaction.atomic():
            # Re-check под локом (используем Booking как точку сериализации).
            from teachers.models import Booking
            b = Booking.objects.select_for_update().get(pk=booking.pk)
            if Transaction.objects.filter(idempotency_key=payout_key).exists():
                return False
            # Если ученику уже сделан refund — выплачивать учителю нельзя
            # (закрывает гонку refund_trial ↔ release_trial_payout).
            if Transaction.objects.filter(
                idempotency_key=f'trial-refund:{b.id}'
            ).exists():
                return False

            trial_price = Decimal(b.trial_price_paid)
            commission_rate = Decimal(settings.PLATFORM_COMMISSION_RATE)
            commission = (trial_price * commission_rate).quantize(Decimal('0.01'))
            teacher_amount = (trial_price - commission).quantize(Decimal('0.01'))

            teacher_user = b.slot.teacher.user
            platform_user = get_or_create_platform_user()

            WalletService.credit(
                user=teacher_user, amount=teacher_amount,
                tx_type=Transaction.Type.LESSON_PAYOUT,
                idempotency_key=payout_key,
                description=f'Выплата за пробный урок {b.id}',
                related_booking=b,
            )
            WalletService.credit(
                user=platform_user, amount=commission,
                tx_type=Transaction.Type.COMMISSION,
                idempotency_key=f'trial-commission:{b.id}',
                description=f'Комиссия с пробного урока {b.id}',
                related_booking=b,
            )
            return True

    @classmethod
    def refund_trial(cls, booking, *, reason: str = '') -> Decimal:
        """Вернуть деньги ученику за платный пробный (cancel/no_show_teacher).

        Идемпотентно по key='trial-refund:<booking.id>'.
        Возвращает сумму возврата (0 если уже возвращена).
        """
        if not booking.is_trial or not booking.trial_price_paid:
            return Decimal('0.00')

        refund_key = f'trial-refund:{booking.id}'
        if Transaction.objects.filter(idempotency_key=refund_key).exists():
            return Decimal('0.00')

        with transaction.atomic():
            # Лочим booking — общая точка сериализации с release_trial_payout,
            # иначе .exists()-проверки гонятся и возможна и выплата, и возврат.
            from teachers.models import Booking
            b = Booking.objects.select_for_update().get(pk=booking.pk)

            # Под локом: повтор refund?
            if Transaction.objects.filter(idempotency_key=refund_key).exists():
                return Decimal('0.00')
            # Если payout учителю уже произошёл — refund-ить уже нельзя.
            if Transaction.objects.filter(
                idempotency_key=f'trial-payout:{b.id}'
            ).exists():
                return Decimal('0.00')

            WalletService.credit(
                user=b.student,
                amount=Decimal(b.trial_price_paid),
                tx_type=Transaction.Type.REFUND,
                idempotency_key=refund_key,
                description=f'Возврат за отменённый пробный урок. {reason}'[:300],
                related_booking=b,
            )
            return Decimal(b.trial_price_paid)


# ---------- DisputeService (ТЗ шаг 8) -------------------------------------


class DisputeError(Exception):
    """Спор нельзя открыть/разрешить в текущем состоянии."""


class DisputeService:
    """Споры по проведённым урокам. Пока спор открыт — выплата заморожена."""

    @staticmethod
    def _is_paid_out(booking) -> bool:
        keys = [f'lesson-payout:{booking.id}', f'trial-payout:{booking.id}']
        return Transaction.objects.filter(idempotency_key__in=keys).exists()

    @classmethod
    def open(cls, booking, *, student, reason: str):
        """Ученик открывает спор по завершённому оплаченному уроку.

        Возможно только: урок completed, деньги ещё в эскроу (не выплачены),
        у урока есть удержанные средства (подписка или платный пробный),
        спор ещё не открывался.
        """
        if booking.student_id != getattr(student, 'id', student):
            raise DisputeError('Спор может открыть только ученик этого урока.')
        if booking.status != 'completed':
            raise DisputeError('Спор можно открыть только по завершённому уроку.')
        has_money = bool(booking.subscription_id) or bool(
            booking.is_trial and booking.trial_price_paid
        )
        if not has_money:
            raise DisputeError('По этому уроку нет удержанных средств.')
        if cls._is_paid_out(booking):
            raise DisputeError('Урок уже оплачен учителю — спор невозможен.')

        with transaction.atomic():
            if LessonDispute.objects.filter(booking_id=booking.id).exists():
                raise DisputeError('Спор по этому уроку уже существует.')
            dispute = LessonDispute.objects.create(
                booking=booking, student=booking.student,
                reason=(reason or '')[:2000], status=LessonDispute.Status.OPEN,
            )
        # Уведомляем учителя + админ-канал косвенно через учителя.
        try:
            teacher_user = booking.slot.teacher.user
            SubscriptionService._notify(
                teacher_user, 'Открыт спор по уроку',
                'Ученик открыл спор по проведённому уроку. Выплата заморожена до решения администрации.',
                _safe_reverse('my_bookings_page'),
            )
        except Exception:
            logger.warning('dispute open notify failed', exc_info=True)
        return dispute

    @classmethod
    def cancel(cls, dispute, *, student):
        """Ученик отзывает свой спор (деньги пойдут учителю в обычном порядке)."""
        with transaction.atomic():
            d = LessonDispute.objects.select_for_update().get(pk=dispute.pk)
            if d.student_id != getattr(student, 'id', student):
                raise DisputeError('Можно отозвать только свой спор.')
            if d.status != LessonDispute.Status.OPEN:
                raise DisputeError('Спор уже разрешён.')
            d.status = LessonDispute.Status.CANCELLED
            d.resolved_at = timezone.now()
            d.save(update_fields=['status', 'resolved_at'])
        return d

    @classmethod
    def resolve_refund(cls, dispute, *, admin, note: str = ''):
        """Админ решает спор в пользу ученика → возврат средств."""
        with transaction.atomic():
            d = (LessonDispute.objects.select_for_update()
                 .select_related('booking').get(pk=dispute.pk))
            if d.status != LessonDispute.Status.OPEN:
                raise DisputeError('Спор уже разрешён.')
            booking = d.booking
            if booking.subscription_id:
                SubscriptionService.refund_lesson(
                    booking, cancelled_by='admin', reason='Спор решён в пользу ученика',
                )
            elif booking.is_trial and booking.trial_price_paid:
                TrialService.refund_trial(booking, reason='Спор решён в пользу ученика')
            d.status = LessonDispute.Status.RESOLVED_REFUND
            d.resolved_by = admin
            d.resolved_at = timezone.now()
            d.admin_note = (note or '')[:1000]
            d.save(update_fields=['status', 'resolved_by', 'resolved_at', 'admin_note'])
        cls._notify_resolution(d, refunded=True)
        return d

    @classmethod
    def resolve_reject(cls, dispute, *, admin, note: str = ''):
        """Админ отклоняет спор → выплата уходит учителю."""
        with transaction.atomic():
            d = (LessonDispute.objects.select_for_update()
                 .select_related('booking').get(pk=dispute.pk))
            if d.status != LessonDispute.Status.OPEN:
                raise DisputeError('Спор уже разрешён.')
            # Сначала закрываем спор (чтобы guard в payout не сработал), потом платим.
            d.status = LessonDispute.Status.RESOLVED_REJECTED
            d.resolved_by = admin
            d.resolved_at = timezone.now()
            d.admin_note = (note or '')[:1000]
            d.save(update_fields=['status', 'resolved_by', 'resolved_at', 'admin_note'])
            booking = d.booking
            try:
                if booking.subscription_id:
                    SubscriptionService.release_lesson_payout(booking)
                elif booking.is_trial and booking.trial_price_paid:
                    TrialService.release_trial_payout(booking)
            except PayoutError as e:
                # Не критично: фоновая задача доведёт выплату (спор уже закрыт).
                logger.warning('dispute reject payout deferred: %s', e)
        cls._notify_resolution(d, refunded=False)
        return d

    @staticmethod
    def _notify_resolution(dispute, *, refunded: bool):
        try:
            title = ('Спор решён в вашу пользу' if refunded
                     else 'Спор отклонён')
            text = ('Администрация вернула средства за оспоренный урок.' if refunded
                    else 'Администрация отклонила спор — урок засчитан, оплата ушла учителю.')
            SubscriptionService._notify(
                dispute.student, title, text, _safe_reverse('my_bookings_page'),
            )
        except Exception:
            logger.warning('dispute resolution notify failed', exc_info=True)
