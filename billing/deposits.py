"""Депозит за разовый урок — политика бронирования и денежный жизненный цикл.

Продуктовые правила (backend — единственный источник истины, фронт лишь
отображает состояние из API, см. BookingPolicyService.evaluate):

  1. Каждый новый ученик получает ОДИН бесплатный пробный урок на всю платформу.
  2. Как только пробный использован (посещён ИЛИ пропущен — неявка), любая
     последующая РАЗОВАЯ бронь требует депозита (BOOKING_DEPOSIT_AMOUNT).
  3. Депозит — не доп. платёж: это и есть оплата разового урока. После completed
     он выплачивается учителю (минус комиссия платформы), ученик не платит сверх.
  4. Неявка ученика → депозит сгорает (уходит учителю), не возвращается.
  5. Вина учителя / урок не состоялся / отмена до урока → депозит возвращается.

Подписочные уроки предоплачены через escrow и депозита НЕ требуют — этот модуль
их не касается.

Денежные движения идут только через WalletService (единственный путь мутации
баланса); статусы депозита живут в billing.models.BookingDeposit. Модуль
намеренно отделён от services.py, чтобы правила бронирования можно было менять
и расширять, не задевая escrow/подписки.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import BookingDeposit, Transaction, Wallet
from .services import PayoutError, WalletService

# Статусы брони, в которых пробный считается «использованным» (глобально по
# ученику). Исключаем только те, что дают ученику ещё одну попытку: отклонён
# учителем и истёк неподтверждённым. Собственная отмена ученика ПОГЛОЩАЕТ пробный
# (анти-абуз против cancel→rebook), как и в TrialService._existing_trial_qs.
TRIAL_NOT_CONSUMED_STATUSES = ('expired', 'cancelled_by_teacher')

# Статусы брони, в которых депозит считается «доставленным» учителю (урок
# состоялся ЛИБО ученик не пришёл — в обоих случаях учитель отработал/ждал).
DEPOSIT_DELIVERED_STATUSES = ('completed', 'no_show_student')


def get_deposit_amount() -> Decimal:
    """Настраиваемая сумма депозита (никогда не хардкодится по коду)."""
    return Decimal(settings.BOOKING_DEPOSIT_AMOUNT)


def has_used_free_trial(student) -> bool:
    """True, если ученик уже израсходовал свой единственный бесплатный пробный.

    Глобально по ученику (один пробный на всю платформу, а не на учителя).
    Пропущенный (неявка) пробный тоже считается использованным.
    """
    from teachers.models import Booking
    return (
        Booking.objects
        .filter(student=student, is_trial=True)
        .exclude(status__in=TRIAL_NOT_CONSUMED_STATUSES)
        .exists()
    )


@dataclass(frozen=True)
class BookingEligibility:
    """Состояние, которое backend отдаёт фронту для отрисовки CTA бронирования."""
    free_trial_available: bool
    deposit_required: bool
    deposit_amount: Decimal

    def as_dict(self) -> dict:
        return {
            'free_trial_available': self.free_trial_available,
            'deposit_required': self.deposit_required,
            'deposit_amount': str(self.deposit_amount),
        }


class BookingPolicyService:
    """Решает, что доступно ученику: бесплатный пробный или бронь с депозитом.

    Единственный источник истины. И API-эндпоинт, и enforcement при создании
    брони спрашивают именно этот сервис — фронт не принимает решений сам.
    """

    @classmethod
    def evaluate(cls, student) -> BookingEligibility:
        """Право ученика на бронь БЕЗ привязки к конкретному слоту/учителю.

        Пока бесплатный пробный не израсходован — он доступен (и депозит не
        нужен). После — любая разовая бронь требует депозита.
        """
        free_trial_available = not has_used_free_trial(student)
        return BookingEligibility(
            free_trial_available=free_trial_available,
            deposit_required=not free_trial_available,
            deposit_amount=get_deposit_amount(),
        )


class DepositService:
    """Денежный жизненный цикл депозита за разовый урок.

    hold → PAID (при бронировании), затем один из терминальных исходов:
      * settle_payout → USED (completed) / FORFEITED (no_show_student) — учителю;
      * refund       → REFUNDED — ученику (вина учителя / не состоялся / отмена).

    Все переходы идемпотентны по idempotency_key транзакции, чтобы повторный
    вызов из Celery/ретрая не двоил деньги.
    """

    # -- создание брони с удержанием депозита --------------------------------

    @classmethod
    def book_with_deposit(
        cls,
        *,
        student,
        slot_id,
        subject,
        message: str = '',
        hold_minutes=None,
    ):
        """Атомарно: Booking.create_hold(разовый) + BookingDeposit(PAID) + debit.

        Депозит удерживается сразу при бронировании (как оплата платного пробного).
        InsufficientFunds откатит всю транзакцию — бронь не создастся, слот не
        зависнет. Возвращает созданный booking.
        """
        from teachers.models import Booking, SlotUnavailable, TimeSlot

        amount = get_deposit_amount()

        with transaction.atomic():
            # Сериализуем попытки ученика по кошельку (как в book_paid_trial):
            # закрывает гонку параллельных броней на границе баланса.
            WalletService.get_or_create_wallet(student)
            Wallet.objects.select_for_update().get(user=student)

            slot = TimeSlot.objects.select_for_update().get(pk=slot_id)
            if slot.status != 'free':
                raise SlotUnavailable(f'Слот занят: {slot.status}')

            booking = Booking.create_hold(
                slot_id=slot_id,
                student=student,
                subject=subject,
                message=message,
                is_trial=False,
                hold_minutes=hold_minutes,
            )
            deposit = BookingDeposit.objects.create(
                booking=booking,
                amount=amount,
                status=BookingDeposit.Status.PENDING,
            )
            # Списываем депозит с ученика (InsufficientFunds → откат всей брони).
            WalletService.debit(
                user=student,
                amount=amount,
                tx_type=Transaction.Type.PURCHASE,
                idempotency_key=f'deposit-hold:{booking.id}',
                description=f'Депозит за урок ({subject.name if subject else "урок"})',
                related_booking=booking,
            )
            deposit.status = BookingDeposit.Status.PAID
            deposit.save(update_fields=['status', 'updated_at'])
            return booking

    # -- выплата учителю после grace window ----------------------------------

    @classmethod
    def settle_payout(cls, booking) -> bool:
        """После grace window: депозит уходит учителю (минус комиссия).

        USED, если урок состоялся (completed); FORFEITED, если ученик не пришёл
        (no_show_student) — в обоих случаях учитель отработал/ждал. Идемпотентно
        по key='deposit-payout:<id>'. Возвращает True, если выплата произошла.
        """
        from .platform_account import get_or_create_platform_user

        deposit = cls._get_deposit(booking)
        if deposit is None:
            raise PayoutError('у брони нет депозита')
        if booking.status not in DEPOSIT_DELIVERED_STATUSES:
            raise PayoutError(
                f'booking.status={booking.status}, ожидался completed/no_show_student'
            )
        # Прощённая неявка (только для подписок) — здесь не бывает, но подстрахуемся.
        if getattr(booking, 'no_show_forgiven', False):
            raise PayoutError('прощённая неявка — депозит не выплачивается')

        from .models import LessonDispute
        if LessonDispute.objects.filter(
            booking_id=booking.id, status=LessonDispute.Status.OPEN,
        ).exists():
            raise PayoutError('по уроку открыт спор — выплата заморожена')

        payout_key = f'deposit-payout:{booking.id}'
        if Transaction.objects.filter(idempotency_key=payout_key).exists():
            return False

        with transaction.atomic():
            from teachers.models import Booking
            b = Booking.objects.select_for_update().get(pk=booking.pk)
            locked = (
                BookingDeposit.objects.select_for_update()
                .select_related('booking')
                .get(pk=deposit.pk)
            )
            if locked.is_terminal:
                return False
            if Transaction.objects.filter(idempotency_key=payout_key).exists():
                return False
            # Если ученику уже вернули депозит — выплачивать учителю нельзя.
            if Transaction.objects.filter(
                idempotency_key=f'deposit-refund:{b.id}'
            ).exists():
                return False

            amount = Decimal(locked.amount)
            commission_rate = Decimal(settings.PLATFORM_COMMISSION_RATE)
            commission = (amount * commission_rate).quantize(Decimal('0.01'))
            teacher_amount = (amount - commission).quantize(Decimal('0.01'))

            teacher_user = b.slot.teacher.user
            platform_user = get_or_create_platform_user()

            WalletService.credit(
                user=teacher_user, amount=teacher_amount,
                tx_type=Transaction.Type.LESSON_PAYOUT,
                idempotency_key=payout_key,
                description=f'Выплата за урок (депозит) {b.id}',
                related_booking=b,
            )
            if commission > 0:
                WalletService.credit(
                    user=platform_user, amount=commission,
                    tx_type=Transaction.Type.COMMISSION,
                    idempotency_key=f'deposit-commission:{b.id}',
                    description=f'Комиссия с урока (депозит) {b.id}',
                    related_booking=b,
                )

            locked.status = (
                BookingDeposit.Status.USED
                if b.status == 'completed'
                else BookingDeposit.Status.FORFEITED
            )
            locked.resolved_at = timezone.now()
            locked.save(update_fields=['status', 'resolved_at', 'updated_at'])
            return True

    # -- возврат ученику -----------------------------------------------------

    @classmethod
    def refund(cls, booking, *, reason: str = '') -> Decimal:
        """Вернуть депозит ученику (вина учителя / не состоялся / отмена/отклонение).

        Идемпотентно по key='deposit-refund:<id>'. Возвращает сумму возврата
        (0 если возвращать нечего или уже выплачено учителю).
        """
        deposit = cls._get_deposit(booking)
        if deposit is None:
            return Decimal('0.00')

        refund_key = f'deposit-refund:{booking.id}'
        if Transaction.objects.filter(idempotency_key=refund_key).exists():
            return Decimal('0.00')

        with transaction.atomic():
            from teachers.models import Booking
            b = Booking.objects.select_for_update().get(pk=booking.pk)
            locked = BookingDeposit.objects.select_for_update().get(pk=deposit.pk)

            if locked.is_terminal:
                return Decimal('0.00')
            if Transaction.objects.filter(idempotency_key=refund_key).exists():
                return Decimal('0.00')
            # Если учителю уже выплатили — возвращать нельзя (закрывает гонку
            # settle_payout ↔ refund).
            if Transaction.objects.filter(
                idempotency_key=f'deposit-payout:{b.id}'
            ).exists():
                return Decimal('0.00')
            # Ничего не удерживали (депозит так и не оплачен) — просто закрываем.
            if locked.status != BookingDeposit.Status.PAID:
                locked.status = BookingDeposit.Status.REFUNDED
                locked.resolved_at = timezone.now()
                locked.save(update_fields=['status', 'resolved_at', 'updated_at'])
                return Decimal('0.00')

            amount = Decimal(locked.amount)
            WalletService.credit(
                user=b.student,
                amount=amount,
                tx_type=Transaction.Type.REFUND,
                idempotency_key=refund_key,
                description=f'Возврат депозита за урок. {reason}'[:300],
                related_booking=b,
            )
            locked.status = BookingDeposit.Status.REFUNDED
            locked.resolved_at = timezone.now()
            locked.save(update_fields=['status', 'resolved_at', 'updated_at'])
            return amount

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _get_deposit(booking):
        return BookingDeposit.objects.filter(booking_id=booking.pk).first()
