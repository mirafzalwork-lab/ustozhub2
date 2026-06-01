"""Атомарные финансовые операции над Wallet с записью в Transaction-ledger.

Все операции:
  * выполняются внутри `transaction.atomic()`
  * блокируют строку Wallet через `select_for_update()` для race-safety
  * пишут запись Transaction (источник правды для аудита)
  * идемпотентны по `idempotency_key` (повторный вызов возвращает существующую транзакцию)
"""
from __future__ import annotations

from datetime import datetime, time as dt_time, timedelta
from decimal import Decimal
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Subscription, Tariff, Transaction, Wallet, WithdrawalRequest

User = get_user_model()


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
                # Гонка: другой процесс успел создать транзакцию между select и create.
                return Transaction.objects.get(idempotency_key=idempotency_key)

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

    # ---- internal helpers ----

    # ---- cancellation / refund -------------------------------------------

    # Какие статусы считаем «активными» — только их можно отменить.
    CANCELLABLE_STATUSES = (
        Subscription.Status.ACTIVE,
        Subscription.Status.PAUSED,
        Subscription.Status.PENDING_PAYMENT,
    )

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

            # 1) Доплатить учителю за completed-но-не-paid уроки.
            unpaid_completed = list(
                Booking.objects
                .filter(subscription=sub, status='completed')
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

    # ---- payout / completion --------------------------------------------

    @classmethod
    def release_lesson_payout(cls, booking) -> bool:
        """Начислить деньги учителю и комиссию платформе за один проведённый урок.

        Атомарно:
          - lock Subscription (select_for_update)
          - проверка no-double-payout (UNIQUE idempotency_key)
          - escrow_balance -= price_per_lesson
          - teacher.wallet  += (1 - commission_rate) * price_per_lesson
          - platform.wallet += commission_rate * price_per_lesson
          - lessons_paid_out += 1
          - если lessons_paid_out == total_lessons → status='completed'

        Возвращает True если payout произошёл, False если уже был сделан раньше.
        """
        from .platform_account import get_or_create_platform_user

        if booking.subscription_id is None:
            raise PayoutError('booking без subscription — нечего выплачивать')
        if booking.status != 'completed':
            raise PayoutError(f'booking.status={booking.status}, ожидался completed')

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
    def _generate_bookings_for_subscription(cls, subscription: Subscription) -> list:
        """Заполняет slots/bookings для подписки из teacher.weekly_schedule.

        Алгоритм:
          - Идём по дням, начиная с завтрашнего, на LOOKAHEAD недель.
          - Для каждого дня берём интервалы из weekly_schedule и режем на куски
            lesson_duration_minutes.
          - Для каждого получившегося куска: либо переиспользуем существующий
            TimeSlot(status=free), либо создаём новый TimeSlot.
          - Помечаем его как 'booked' и создаём Booking(status='confirmed').
          - Останавливаемся, когда набрали total_lessons.
        """
        from teachers.models import Booking, TimeSlot

        teacher = subscription.teacher
        student = subscription.student
        subject = subscription.subject
        duration = timedelta(minutes=subscription.lesson_duration_minutes)
        needed = subscription.total_lessons

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
    def _existing_trial_qs(student, teacher, subject):
        """Уже существующий пробный (любой непустой статус) у этого ученика."""
        from teachers.models import Booking
        return Booking.objects.filter(
            student=student,
            slot__teacher=teacher,
            subject=subject,
            is_trial=True,
        ).exclude(status__in=['expired', 'cancelled_by_student', 'cancelled_by_teacher'])

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
            existing = cls._existing_trial_qs(student, teacher, subject).first()
            if existing is not None:
                raise TrialAlreadyTaken(
                    'У вас уже есть пробный урок с этим учителем по этому предмету.'
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
        if booking.status != 'completed':
            raise PayoutError(f'booking.status={booking.status}, ожидался completed')

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
