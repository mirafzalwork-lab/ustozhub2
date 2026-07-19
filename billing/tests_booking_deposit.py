"""Исчерпывающие тесты депозита за разовый урок (billing.deposits).

Структура:
  * CoreScenariosTests        — пять сценариев ТЗ (карта требований 1→5).
  * PolicyEligibilityTests    — BookingPolicyService / has_used_free_trial.
  * FreeTrialConsumptionTests — какие статусы брони «съедают» бесплатный пробный.
  * BookingCreationTests      — enforcement в API и DepositService.book_with_deposit.
  * SettlePayoutTests         — USED/FORFEITED, комиссия, гарды, заморозка спором.
  * RefundTests               — REFUNDED, гарды, идемпотентность.
  * LifecycleIntegrationTests — settle-обработчики, expire, reject, mark/release задачи.
  * IdempotencyRaceTests      — payout↔refund не двоят и не теряют деньги.
  * LedgerInvariantTests      — баланс == сумма ledger после полного цикла.
  * ConfigModelTests          — настраиваемость суммы, ограничения модели, enum.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.deposits import (
    BookingPolicyService,
    DepositService,
    get_deposit_amount,
    has_used_free_trial,
)
from billing.models import BookingDeposit, LessonDispute, Transaction, Wallet
from billing.platform_account import get_or_create_platform_user
from billing.services import PayoutError, WalletService
from billing.tasks import release_pending_payouts
from billing.tests import (
    SIMPLE_STATIC_STORAGES,
    _attend,
    _make_student_with_balance,
    _make_teacher_with_subject,
)
from teachers.models import (
    Booking,
    LessonAttendanceSession,
    TeacherSubject,
    TimeSlot,
)

PWD = 'x' * 12
DEPOSIT = Decimal('30000')
TEACHER_SHARE = Decimal('25500.00')   # 30000 * 0.85
COMMISSION = Decimal('4500.00')       # 30000 * 0.15


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES, RATELIMIT_ENABLE=False)
class DepositTestBase(TestCase):
    """Общий setUp + помощники для всех групп тестов депозита."""

    def setUp(self):
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('dep_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('dep_s', balance=Decimal('200000'))

    # -- слоты / брони -------------------------------------------------------

    def _future_slot(self, minutes_ahead=48 * 60, dur=60):
        start = timezone.now() + timedelta(minutes=minutes_ahead)
        return TimeSlot.objects.create(
            teacher=self.teacher, start_at=start,
            end_at=start + timedelta(minutes=dur), status='free',
        )

    def _make_confirmed_jitsi(self, booking):
        booking.confirm()
        booking.refresh_from_db()
        booking.meeting_url = booking.build_meeting_url()
        booking.save(update_fields=['meeting_url'])
        return booking

    def _mark_trial_used(self, student=None):
        """Дёшево помечает пробный израсходованным (completed is_trial-бронь)."""
        student = student or self.student
        past = timezone.now() - timedelta(days=3)
        sl = TimeSlot.objects.create(
            teacher=self.teacher, start_at=past,
            end_at=past + timedelta(minutes=60), status='booked',
        )
        return Booking.objects.create(
            slot=sl, student=student, subject=self.subject,
            is_trial=True, status='completed',
        )

    def _deposit_booking(self, *, student=None, status=None, confirm=False):
        """Создаёт разовую бронь с удержанным депозитом (PAID)."""
        student = student or self.student
        slot = self._future_slot()
        booking = DepositService.book_with_deposit(
            student=student, slot_id=slot.id, subject=self.subject,
        )
        if confirm:
            self._make_confirmed_jitsi(booking)
        if status is not None:
            Booking.objects.filter(pk=booking.pk).update(status=status)
            booking.refresh_from_db()
        return booking

    # -- деньги --------------------------------------------------------------

    def _balance(self, user):
        user.wallet.refresh_from_db()
        return user.wallet.balance

    def _deposit(self, booking):
        return BookingDeposit.objects.get(booking=booking)


# ═══════════════════════ 1. Пять сценариев ТЗ ══════════════════════════════


class CoreScenariosTests(DepositTestBase):

    def _consume_free_trial_attended(self):
        slot = self._future_slot()
        b = Booking.create_hold(slot_id=slot.id, student=self.student,
                                subject=self.subject, is_trial=True)
        self._make_confirmed_jitsi(b)
        _attend(b, 'teacher', fraction=1.0)
        _attend(b, 'student', fraction=1.0)
        self.assertEqual(b.settle_after_end(), 'completed')
        return b

    def test_1_new_student_first_trial_free(self):
        elig = BookingPolicyService.evaluate(self.student)
        self.assertTrue(elig.free_trial_available)
        self.assertFalse(elig.deposit_required)
        before = self._balance(self.student)
        slot = self._future_slot()
        self.client.login(username='dep_s', password=PWD)
        r = self.client.post(
            reverse('booking_create_api'),
            data={'slot_id': str(slot.id), 'subject_id': self.subject.id,
                  'is_trial': True},
            content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        b = Booking.objects.get(slot=slot)
        self.assertTrue(b.is_trial)
        self.assertFalse(BookingDeposit.objects.filter(booking=b).exists())
        self.assertEqual(self._balance(self.student), before)

    def test_2_attended_trial_next_requires_deposit(self):
        self._consume_free_trial_attended()
        elig = BookingPolicyService.evaluate(self.student)
        self.assertFalse(elig.free_trial_available)
        self.assertTrue(elig.deposit_required)
        before = self._balance(self.student)
        slot = self._future_slot()
        self.client.login(username='dep_s', password=PWD)
        r = self.client.post(
            reverse('booking_create_api'),
            data={'slot_id': str(slot.id), 'subject_id': self.subject.id},
            content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        b = Booking.objects.get(slot=slot)
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.PAID)
        self.assertEqual(self._balance(self.student), before - DEPOSIT)

    def test_3_deposit_attended_applied_to_teacher(self):
        self._mark_trial_used()
        b = self._deposit_booking(confirm=True)
        _attend(b, 'teacher', fraction=1.0)
        _attend(b, 'student', fraction=1.0)
        self.assertEqual(b.settle_after_end(), 'completed')
        t0, p0 = self._balance(self.teacher.user), self._balance(self.platform)
        self.assertTrue(DepositService.settle_payout(b))
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.USED)
        self.assertEqual(self._balance(self.teacher.user), t0 + TEACHER_SHARE)
        self.assertEqual(self._balance(self.platform), p0 + COMMISSION)

    def test_4_deposit_no_show_forfeited(self):
        self._mark_trial_used()
        b = self._deposit_booking(confirm=True)
        after_hold = self._balance(self.student)
        _attend(b, 'teacher', fraction=1.0)
        self.assertEqual(b.settle_after_end(), 'no_show_student')
        t0 = self._balance(self.teacher.user)
        self.assertTrue(DepositService.settle_payout(b))
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.FORFEITED)
        self.assertEqual(self._balance(self.student), after_hold)   # НЕ возвращён
        self.assertEqual(self._balance(self.teacher.user), t0 + TEACHER_SHARE)

    def test_5_trial_no_show_consumes_trial(self):
        slot = self._future_slot()
        b = Booking.create_hold(slot_id=slot.id, student=self.student,
                                subject=self.subject, is_trial=True)
        self._make_confirmed_jitsi(b)
        _attend(b, 'teacher', fraction=1.0)
        self.assertEqual(b.settle_after_end(), 'no_show_student')
        self.assertTrue(has_used_free_trial(self.student))
        elig = BookingPolicyService.evaluate(self.student)
        self.assertFalse(elig.free_trial_available)
        self.assertTrue(elig.deposit_required)


# ═══════════════════════ 2. Политика / eligibility ═════════════════════════


class PolicyEligibilityTests(DepositTestBase):

    def test_new_student_eligibility(self):
        e = BookingPolicyService.evaluate(self.student)
        self.assertTrue(e.free_trial_available)
        self.assertFalse(e.deposit_required)
        self.assertEqual(e.deposit_amount, DEPOSIT)

    def test_after_trial_eligibility(self):
        self._mark_trial_used()
        e = BookingPolicyService.evaluate(self.student)
        self.assertFalse(e.free_trial_available)
        self.assertTrue(e.deposit_required)

    def test_eligibility_is_per_student(self):
        other = _make_student_with_balance('dep_s2', balance=Decimal('0'))
        self._mark_trial_used(student=self.student)
        # Пробный self.student не влияет на другого ученика.
        self.assertTrue(has_used_free_trial(self.student))
        self.assertFalse(has_used_free_trial(other))
        self.assertTrue(BookingPolicyService.evaluate(other).free_trial_available)

    def test_eligibility_api_new_student(self):
        self.client.login(username='dep_s', password=PWD)
        r = self.client.get(reverse('booking_eligibility_api'))
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d['free_trial_available'])
        self.assertFalse(d['deposit_required'])
        self.assertEqual(Decimal(d['deposit_amount']), DEPOSIT)
        self.assertEqual(Decimal(d['wallet_balance']), Decimal('200000'))
        self.assertTrue(d['sufficient_balance'])

    def test_eligibility_api_after_trial_and_low_balance(self):
        poor = _make_student_with_balance('dep_poor', balance=Decimal('100'))
        self._mark_trial_used(student=poor)
        self.client.force_login(poor)
        r = self.client.get(reverse('booking_eligibility_api'))
        d = r.json()
        self.assertFalse(d['free_trial_available'])
        self.assertTrue(d['deposit_required'])
        self.assertFalse(d['sufficient_balance'])

    def test_eligibility_api_requires_student(self):
        # Учитель — не ученик: доступ к eligibility запрещён.
        self.client.login(username='dep_t', password=PWD)
        r = self.client.get(reverse('booking_eligibility_api'))
        self.assertIn(r.status_code, (302, 403))


# ═══════════════ 3. Что «съедает» бесплатный пробный ═══════════════════════


class FreeTrialConsumptionTests(DepositTestBase):

    def _trial_with_status(self, status):
        past = timezone.now() - timedelta(days=2)
        sl = TimeSlot.objects.create(teacher=self.teacher, start_at=past,
                                     end_at=past + timedelta(minutes=60), status='booked')
        return Booking.objects.create(slot=sl, student=self.student,
                                      subject=self.subject, is_trial=True, status=status)

    def test_completed_consumes(self):
        self._trial_with_status('completed')
        self.assertTrue(has_used_free_trial(self.student))

    def test_no_show_student_consumes(self):
        self._trial_with_status('no_show_student')
        self.assertTrue(has_used_free_trial(self.student))

    def test_not_held_consumes(self):
        self._trial_with_status('not_held')
        self.assertTrue(has_used_free_trial(self.student))

    def test_cancelled_by_student_consumes(self):
        # Анти-абуз: собственная отмена не даёт «переиграть» пробный.
        self._trial_with_status('cancelled_by_student')
        self.assertTrue(has_used_free_trial(self.student))

    def test_pending_and_confirmed_consume(self):
        self._trial_with_status('pending')
        self.assertTrue(has_used_free_trial(self.student))

    def test_cancelled_by_teacher_does_not_consume(self):
        # Отказ учителя → ученику дают ещё попытку.
        self._trial_with_status('cancelled_by_teacher')
        self.assertFalse(has_used_free_trial(self.student))

    def test_expired_does_not_consume(self):
        # Неподтверждённый и истёкший пробный не считается использованным.
        self._trial_with_status('expired')
        self.assertFalse(has_used_free_trial(self.student))

    def test_non_trial_booking_does_not_consume(self):
        past = timezone.now() - timedelta(days=2)
        sl = TimeSlot.objects.create(teacher=self.teacher, start_at=past,
                                     end_at=past + timedelta(minutes=60), status='booked')
        Booking.objects.create(slot=sl, student=self.student, subject=self.subject,
                               is_trial=False, status='completed')
        self.assertFalse(has_used_free_trial(self.student))


# ═══════════════ 4. Создание брони и enforcement ═══════════════════════════


class BookingCreationTests(DepositTestBase):

    def test_first_booking_is_free_even_without_is_trial_flag(self):
        # Новый ученик, обычная бронь (is_trial не задан) → бесплатный пробный.
        before = self._balance(self.student)
        slot = self._future_slot()
        self.client.login(username='dep_s', password=PWD)
        r = self.client.post(reverse('booking_create_api'),
                             data={'slot_id': str(slot.id), 'subject_id': self.subject.id},
                             content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        b = Booking.objects.get(slot=slot)
        self.assertTrue(b.is_trial)
        self.assertFalse(BookingDeposit.objects.filter(booking=b).exists())
        self.assertEqual(self._balance(self.student), before)

    def test_explicit_trial_when_used_returns_409(self):
        self._mark_trial_used()
        slot = self._future_slot()
        self.client.login(username='dep_s', password=PWD)
        r = self.client.post(reverse('booking_create_api'),
                             data={'slot_id': str(slot.id), 'subject_id': self.subject.id,
                                   'is_trial': True},
                             content_type='application/json')
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json().get('code'), 'free_trial_used')
        self.assertFalse(Booking.objects.filter(slot=slot).exists())

    def test_deposit_booking_holds_exact_amount(self):
        self._mark_trial_used()
        before = self._balance(self.student)
        b = self._deposit_booking()
        d = self._deposit(b)
        self.assertEqual(d.status, BookingDeposit.Status.PAID)
        self.assertEqual(d.amount, DEPOSIT)
        self.assertEqual(b.status, 'pending')
        self.assertFalse(b.is_trial)
        self.assertEqual(self._balance(self.student), before - DEPOSIT)
        # Транзакция удержания привязана к брони.
        self.assertTrue(Transaction.objects.filter(
            idempotency_key=f'deposit-hold:{b.id}').exists())

    def test_insufficient_funds_blocks_booking_via_api(self):
        poor = _make_student_with_balance('dep_poor', balance=Decimal('0'))
        self._mark_trial_used(student=poor)
        slot = self._future_slot()
        self.client.force_login(poor)
        r = self.client.post(reverse('booking_create_api'),
                             data={'slot_id': str(slot.id), 'subject_id': self.subject.id},
                             content_type='application/json')
        self.assertEqual(r.status_code, 402, r.content)
        self.assertEqual(r.json().get('code'), 'insufficient_funds')
        self.assertFalse(Booking.objects.filter(slot=slot).exists())
        slot.refresh_from_db()
        self.assertEqual(slot.status, 'free')
        self.assertFalse(BookingDeposit.objects.filter(booking__slot=slot).exists())

    def test_balance_exactly_deposit_succeeds(self):
        exact = _make_student_with_balance('dep_exact', balance=DEPOSIT)
        self._mark_trial_used(student=exact)
        slot = self._future_slot()
        b = DepositService.book_with_deposit(student=exact, slot_id=slot.id,
                                             subject=self.subject)
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.PAID)
        self.assertEqual(self._balance(exact), Decimal('0.00'))

    def test_balance_one_below_deposit_fails(self):
        poor = _make_student_with_balance('dep_near', balance=DEPOSIT - Decimal('1'))
        self._mark_trial_used(student=poor)
        slot = self._future_slot()
        from billing.services import InsufficientFunds
        with self.assertRaises(InsufficientFunds):
            DepositService.book_with_deposit(student=poor, slot_id=slot.id,
                                             subject=self.subject)
        slot.refresh_from_db()
        self.assertEqual(slot.status, 'free')
        self.assertEqual(self._balance(poor), DEPOSIT - Decimal('1'))

    def test_taken_slot_raises_and_no_debit(self):
        self._mark_trial_used()
        slot = self._future_slot()
        # Занимаем слот другой бронью.
        Booking.create_hold(slot_id=slot.id, student=self.student, subject=self.subject)
        before = self._balance(self.student)
        from teachers.models import SlotUnavailable
        with self.assertRaises(SlotUnavailable):
            DepositService.book_with_deposit(student=self.student, slot_id=slot.id,
                                             subject=self.subject)
        self.assertEqual(self._balance(self.student), before)

    def test_paid_trial_offering_unaffected(self):
        # Платный пробный учителя не превращается в депозит и списывает trial_price.
        ts = TeacherSubject.objects.get(teacher=self.teacher, subject=self.subject)
        ts.is_free_trial = False
        ts.trial_price = Decimal('50000')
        ts.save(update_fields=['is_free_trial', 'trial_price'])
        before = self._balance(self.student)
        slot = self._future_slot()
        self.client.login(username='dep_s', password=PWD)
        r = self.client.post(reverse('booking_create_api'),
                             data={'slot_id': str(slot.id), 'subject_id': self.subject.id,
                                   'is_trial': True},
                             content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        b = Booking.objects.get(slot=slot)
        self.assertTrue(b.is_trial)
        self.assertEqual(b.trial_price_paid, Decimal('50000'))
        self.assertFalse(BookingDeposit.objects.filter(booking=b).exists())
        self.assertEqual(self._balance(self.student), before - Decimal('50000'))


# ═══════════════════════ 5. Выплата (USED / FORFEITED) ═════════════════════


class SettlePayoutTests(DepositTestBase):

    def test_completed_marks_used_and_pays_teacher(self):
        self._mark_trial_used()
        b = self._deposit_booking(status='completed')
        t0, p0 = self._balance(self.teacher.user), self._balance(self.platform)
        self.assertTrue(DepositService.settle_payout(b))
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.USED)
        self.assertEqual(self._balance(self.teacher.user), t0 + TEACHER_SHARE)
        self.assertEqual(self._balance(self.platform), p0 + COMMISSION)

    def test_no_show_marks_forfeited_and_pays_teacher(self):
        self._mark_trial_used()
        b = self._deposit_booking(status='no_show_student')
        t0 = self._balance(self.teacher.user)
        self.assertTrue(DepositService.settle_payout(b))
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.FORFEITED)
        self.assertEqual(self._balance(self.teacher.user), t0 + TEACHER_SHARE)

    def test_payout_idempotent(self):
        self._mark_trial_used()
        b = self._deposit_booking(status='completed')
        t0 = self._balance(self.teacher.user)
        self.assertTrue(DepositService.settle_payout(b))
        self.assertFalse(DepositService.settle_payout(b))   # второй раз — no-op
        self.assertEqual(self._balance(self.teacher.user), t0 + TEACHER_SHARE)

    def test_payout_raises_without_deposit(self):
        # Бесплатный пробный (без депозита) — settle_payout не применим.
        slot = self._future_slot()
        b = Booking.create_hold(slot_id=slot.id, student=self.student,
                                subject=self.subject, is_trial=True)
        Booking.objects.filter(pk=b.pk).update(status='completed')
        b.refresh_from_db()
        with self.assertRaises(PayoutError):
            DepositService.settle_payout(b)

    def test_payout_raises_when_not_delivered(self):
        self._mark_trial_used()
        b = self._deposit_booking(status='confirmed')
        with self.assertRaises(PayoutError):
            DepositService.settle_payout(b)

    def test_payout_frozen_by_open_dispute(self):
        self._mark_trial_used()
        b = self._deposit_booking(status='completed')
        LessonDispute.objects.create(booking=b, student=self.student,
                                     reason='спор', status=LessonDispute.Status.OPEN)
        with self.assertRaises(PayoutError):
            DepositService.settle_payout(b)
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.PAID)

    def test_commission_rounding_conserves_money(self):
        # Нечётная сумма: учитель + комиссия = ровно депозит (деньги не теряются).
        with override_settings(BOOKING_DEPOSIT_AMOUNT=Decimal('33333')):
            rich = _make_student_with_balance('dep_odd', balance=Decimal('100000'))
            self._mark_trial_used(student=rich)
            b = self._deposit_booking(student=rich, status='completed')
            t0, p0 = self._balance(self.teacher.user), self._balance(self.platform)
            self.assertTrue(DepositService.settle_payout(b))
            gained = (self._balance(self.teacher.user) - t0) + (self._balance(self.platform) - p0)
            self.assertEqual(gained, Decimal('33333'))


# ═══════════════════════ 6. Возврат (REFUNDED) ════════════════════════════


class RefundTests(DepositTestBase):

    def test_refund_credits_student_and_marks_refunded(self):
        self._mark_trial_used()
        b = self._deposit_booking()
        s0 = self._balance(self.student)
        self.assertEqual(DepositService.refund(b, reason='тест'), DEPOSIT)
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.REFUNDED)
        self.assertEqual(self._balance(self.student), s0 + DEPOSIT)

    def test_refund_idempotent(self):
        self._mark_trial_used()
        b = self._deposit_booking()
        s0 = self._balance(self.student)
        self.assertEqual(DepositService.refund(b, reason='1'), DEPOSIT)
        self.assertEqual(DepositService.refund(b, reason='2'), Decimal('0.00'))
        self.assertEqual(self._balance(self.student), s0 + DEPOSIT)

    def test_refund_noop_when_no_deposit(self):
        slot = self._future_slot()
        b = Booking.create_hold(slot_id=slot.id, student=self.student,
                                subject=self.subject, is_trial=True)
        self.assertEqual(DepositService.refund(b, reason='нет депозита'), Decimal('0.00'))

    def test_refund_blocked_after_payout(self):
        self._mark_trial_used()
        b = self._deposit_booking(status='completed')
        self.assertTrue(DepositService.settle_payout(b))
        s0 = self._balance(self.student)
        # Депозит уже выплачен учителю → возврат невозможен.
        self.assertEqual(DepositService.refund(b, reason='поздно'), Decimal('0.00'))
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.USED)
        self.assertEqual(self._balance(self.student), s0)


# ═══════════════ 7. Интеграция жизненного цикла ════════════════════════════


class LifecycleIntegrationTests(DepositTestBase):

    def test_teacher_no_show_refunds_deposit(self):
        from teachers.tasks import _refund_teacher_no_show
        self._mark_trial_used()
        b = self._deposit_booking(confirm=True)
        after_hold = self._balance(self.student)
        _attend(b, 'student', fraction=1.0)
        self.assertEqual(b.settle_after_end(), 'no_show_teacher')
        _refund_teacher_no_show(b)
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.REFUNDED)
        self.assertEqual(self._balance(self.student), after_hold + DEPOSIT)

    def test_not_held_refunds_deposit(self):
        from teachers.tasks import _handle_not_held
        self._mark_trial_used()
        b = self._deposit_booking(status='not_held')
        s0 = self._balance(self.student)
        _handle_not_held(b)
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.REFUNDED)
        self.assertEqual(self._balance(self.student), s0 + DEPOSIT)

    def test_cancel_before_lesson_refunds_deposit(self):
        self._mark_trial_used()
        b = self._deposit_booking()
        s0 = self._balance(self.student)
        self.client.login(username='dep_s', password=PWD)
        r = self.client.post(reverse('booking_cancel_api', args=[b.id]))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.REFUNDED)
        self.assertEqual(self._balance(self.student), s0 + DEPOSIT)

    def test_teacher_reject_refunds_deposit(self):
        self._mark_trial_used()
        b = self._deposit_booking()   # pending
        s0 = self._balance(self.student)
        self.client.login(username='dep_t', password=PWD)
        r = self.client.post(reverse('booking_reject_api', args=[b.id]),
                             data={'reply': 'занят'}, content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        b.refresh_from_db()
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.REFUNDED)
        self.assertEqual(self._balance(self.student), s0 + DEPOSIT)

    def test_expire_hold_refunds_deposit(self):
        self._mark_trial_used()
        b = self._deposit_booking()   # pending + hold
        s0 = self._balance(self.student)
        b.expire()
        b.refresh_from_db()
        self.assertEqual(b.status, 'expired')
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.REFUNDED)
        self.assertEqual(self._balance(self.student), s0 + DEPOSIT)

    def test_release_pending_payouts_pays_after_grace(self):
        self._mark_trial_used()
        b = self._deposit_booking(status='completed')
        TimeSlot.objects.filter(pk=b.slot_id).update(
            start_at=timezone.now() - timedelta(hours=8),
            end_at=timezone.now() - timedelta(hours=7), status='booked')
        t0 = self._balance(self.teacher.user)
        res = release_pending_payouts()
        self.assertGreaterEqual(res['paid'], 1)
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.USED)
        self.assertEqual(self._balance(self.teacher.user), t0 + TEACHER_SHARE)

    def test_release_pending_payouts_skips_before_grace(self):
        self._mark_trial_used()
        b = self._deposit_booking(status='completed')
        TimeSlot.objects.filter(pk=b.slot_id).update(
            start_at=timezone.now() - timedelta(minutes=40),
            end_at=timezone.now() - timedelta(minutes=1), status='booked')
        t0 = self._balance(self.teacher.user)
        release_pending_payouts()
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.PAID)
        self.assertEqual(self._balance(self.teacher.user), t0)

    def test_release_pending_payouts_frozen_by_dispute(self):
        self._mark_trial_used()
        b = self._deposit_booking(status='completed')
        TimeSlot.objects.filter(pk=b.slot_id).update(
            start_at=timezone.now() - timedelta(hours=8),
            end_at=timezone.now() - timedelta(hours=7), status='booked')
        LessonDispute.objects.create(booking=b, student=self.student,
                                     reason='спор', status=LessonDispute.Status.OPEN)
        t0 = self._balance(self.teacher.user)
        release_pending_payouts()
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.PAID)
        self.assertEqual(self._balance(self.teacher.user), t0)

    def test_mark_completed_then_release_end_to_end(self):
        from teachers.tasks import mark_completed_lessons
        self._mark_trial_used()
        b = self._deposit_booking(confirm=True)
        # Слот закончился 40 мин назад, оба присутствовали полностью.
        start = timezone.now() - timedelta(minutes=100)
        end = timezone.now() - timedelta(minutes=40)
        TimeSlot.objects.filter(pk=b.slot_id).update(start_at=start, end_at=end)
        b.refresh_from_db()
        for role in ('teacher', 'student'):
            LessonAttendanceSession.objects.create(booking=b, role=role,
                                                    joined_at=start, left_at=end)
        mark_completed_lessons()
        b.refresh_from_db()
        self.assertEqual(b.status, 'completed')
        # До grace выплаты нет.
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.PAID)
        # Сдвигаем за grace и выплачиваем (start и end согласованно в прошлом).
        TimeSlot.objects.filter(pk=b.slot_id).update(
            start_at=timezone.now() - timedelta(hours=8),
            end_at=timezone.now() - timedelta(hours=7))
        t0 = self._balance(self.teacher.user)
        release_pending_payouts()
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.USED)
        self.assertEqual(self._balance(self.teacher.user), t0 + TEACHER_SHARE)


# ═══════════════ 7b. Разрешение спора по депозитной броне ══════════════════


class DisputeResolutionTests(DepositTestBase):
    """Регресс на дыру аудита: resolve_refund/reject не обрабатывали депозит."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth import get_user_model
        self.admin = get_user_model().objects.create_user(
            username='dep_admin', email='a@x.com', password=PWD,
            is_staff=True, is_superuser=True)

    def _disputed_deposit_booking(self):
        self._mark_trial_used()
        b = self._deposit_booking(status='completed')
        d = LessonDispute.objects.create(
            booking=b, student=self.student, reason='не понравилось',
            status=LessonDispute.Status.OPEN)
        return b, d

    def test_dispute_refund_returns_deposit_and_blocks_payout(self):
        from billing.services import DisputeService
        b, d = self._disputed_deposit_booking()
        s0, t0 = self._balance(self.student), self._balance(self.teacher.user)
        DisputeService.resolve_refund(d, admin=self.admin, note='в пользу ученика')
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.REFUNDED)
        self.assertEqual(self._balance(self.student), s0 + DEPOSIT)
        # КЛЮЧЕВОЕ: последующая фоновая выплата НЕ отдаёт депозит учителю.
        TimeSlot.objects.filter(pk=b.slot_id).update(
            start_at=timezone.now() - timedelta(hours=8),
            end_at=timezone.now() - timedelta(hours=7), status='booked')
        release_pending_payouts()
        self.assertEqual(self._balance(self.teacher.user), t0)
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.REFUNDED)

    def test_dispute_reject_pays_teacher(self):
        from billing.services import DisputeService
        b, d = self._disputed_deposit_booking()
        t0 = self._balance(self.teacher.user)
        DisputeService.resolve_reject(d, admin=self.admin, note='спор отклонён')
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.USED)
        self.assertEqual(self._balance(self.teacher.user), t0 + TEACHER_SHARE)


# ═══════════════ 8. Идемпотентность и гонки payout↔refund ══════════════════


class IdempotencyRaceTests(DepositTestBase):

    def test_payout_after_refund_is_blocked(self):
        self._mark_trial_used()
        b = self._deposit_booking(status='completed')
        self.assertEqual(DepositService.refund(b, reason='возврат первым'), DEPOSIT)
        t0 = self._balance(self.teacher.user)
        # Возврат уже сделан → выплата учителю невозможна.
        self.assertFalse(DepositService.settle_payout(b))
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.REFUNDED)
        self.assertEqual(self._balance(self.teacher.user), t0)

    def test_refund_after_payout_is_blocked(self):
        self._mark_trial_used()
        b = self._deposit_booking(status='completed')
        self.assertTrue(DepositService.settle_payout(b))
        s0 = self._balance(self.student)
        self.assertEqual(DepositService.refund(b, reason='поздно'), Decimal('0.00'))
        self.assertEqual(self._deposit(b).status, BookingDeposit.Status.USED)
        self.assertEqual(self._balance(self.student), s0)

    def test_double_book_with_deposit_holds_once_per_booking(self):
        # Каждая бронь — свой депозит и своё удержание; ключи не пересекаются.
        self._mark_trial_used()
        b1 = self._deposit_booking()
        b2 = self._deposit_booking()
        self.assertNotEqual(b1.id, b2.id)
        self.assertEqual(Transaction.objects.filter(
            idempotency_key__in=[f'deposit-hold:{b1.id}',
                                 f'deposit-hold:{b2.id}']).count(), 2)


# ═══════════════════════ 9. Инвариант леджера ══════════════════════════════


class LedgerInvariantTests(DepositTestBase):

    def _assert_reconciled(self, user):
        w = Wallet.objects.get(user=user)
        self.assertEqual(w.balance, WalletService.reconcile_balance(w))

    def test_ledger_consistent_after_payout_cycle(self):
        self._mark_trial_used()
        b = self._deposit_booking(status='completed')
        DepositService.settle_payout(b)
        for u in (self.student, self.teacher.user, self.platform):
            self._assert_reconciled(u)

    def test_ledger_consistent_after_refund_cycle(self):
        self._mark_trial_used()
        b = self._deposit_booking()
        DepositService.refund(b, reason='цикл')
        self._assert_reconciled(self.student)
        # Возврат вернул ровно удержанное — нетто по ученику ноль относительно депозита.
        self.assertEqual(self._balance(self.student), Decimal('200000'))


# ═══════════════════════ 10. Конфиг и модель ═══════════════════════════════


class ConfigModelTests(DepositTestBase):

    def test_amount_configurable(self):
        with override_settings(BOOKING_DEPOSIT_AMOUNT=Decimal('50000')):
            self.assertEqual(get_deposit_amount(), Decimal('50000'))
            self.assertEqual(
                BookingPolicyService.evaluate(self.student).deposit_amount,
                Decimal('50000'))

    def test_configured_amount_is_actually_held(self):
        with override_settings(BOOKING_DEPOSIT_AMOUNT=Decimal('45000')):
            self._mark_trial_used()
            before = self._balance(self.student)
            b = self._deposit_booking()
            self.assertEqual(self._deposit(b).amount, Decimal('45000'))
            self.assertEqual(self._balance(self.student), before - Decimal('45000'))

    def test_one_deposit_per_booking(self):
        self._mark_trial_used()
        b = self._deposit_booking()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BookingDeposit.objects.create(booking=b, amount=DEPOSIT,
                                              status=BookingDeposit.Status.PENDING)

    def test_amount_must_be_positive(self):
        self._mark_trial_used()
        b = self._deposit_booking()
        BookingDeposit.objects.filter(booking=b).delete()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BookingDeposit.objects.create(booking=b, amount=Decimal('0'),
                                              status=BookingDeposit.Status.PENDING)

    def test_terminal_flag(self):
        d = BookingDeposit(amount=DEPOSIT, status=BookingDeposit.Status.PAID)
        self.assertFalse(d.is_terminal)
        for s in (BookingDeposit.Status.USED, BookingDeposit.Status.FORFEITED,
                  BookingDeposit.Status.REFUNDED):
            d.status = s
            self.assertTrue(d.is_terminal)
