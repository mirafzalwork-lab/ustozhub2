"""Исчерпывающие тесты новой логики присутствия, завершения урока и выплат.

Покрывает:
  * Booking.compute_attendance — математика длительностей и overlap (мердж
    реконнект-интервалов, кламп открытых сессий, пересечения);
  * Booking.settle_after_end — полное дерево решений по порогу 40% и overlap,
    граничные значения, анти-фрод-сценарии (§5), внешние ссылки, гард по статусу;
  * record_join / record_leave — открытие/закрытие сессий, реконнекты, гарды;
  * последствия для денег — completed платит учителю, not_held/forgiven — нет,
    consumed-неявка платит; sweeper release_pending_payouts уважает grace=6ч и
    исключает not_held.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from billing.platform_account import get_or_create_platform_user
from billing.services import PayoutError, SubscriptionService
from billing.tasks import release_pending_payouts
from teachers.models import Booking, LessonAttendanceSession, LessonEvent, TimeSlot

from .tests import (
    _make_student_with_balance,
    _make_tariff,
    _make_teacher_with_subject,
)

ROLE_T = LessonAttendanceSession.ROLE_TEACHER
ROLE_S = LessonAttendanceSession.ROLE_STUDENT


class _AttendBase(TestCase):
    """Подписка + один Jitsi-урок с управляемым прошедшим окном."""

    PREFIX = 'att'

    def setUp(self):
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject(f'{self.PREFIX}_t')
        self.tariff = _make_tariff(self.teacher, self.subject)  # 100000/урок, 8 уроков
        self.student = _make_student_with_balance(
            f'{self.PREFIX}_s', balance=Decimal('1000000'))
        self.sub = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key=f'{self.PREFIX}-purchase',
        )
        self.booking = (
            Booking.objects.filter(subscription=self.sub)
            .select_related('slot').first()
        )
        self.booking.meeting_url = self.booking.build_meeting_url()  # наш Jitsi
        self.booking.save(update_fields=['meeting_url'])

    def _window(self, lesson_min=60, end_ago_min=90):
        """Ставит слот в прошлое: урок lesson_min мин, закончился end_ago_min назад."""
        end = timezone.now() - timedelta(minutes=end_ago_min)
        start = end - timedelta(minutes=lesson_min)
        self.booking.slot.start_at = start
        self.booking.slot.end_at = end
        self.booking.slot.save(update_fields=['start_at', 'end_at'])
        self.booking.refresh_from_db()
        self.lesson_seconds = lesson_min * 60
        self.required = lesson_min * 60 * 0.4
        return self.booking

    def _ses(self, role, start_min, end_min=None):
        """Сессия присутствия [start+start_min, start+end_min] (минуты). end=None → открытая."""
        base = self.booking.slot.start_at
        return LessonAttendanceSession.objects.create(
            booking=self.booking, role=role,
            joined_at=base + timedelta(minutes=start_min),
            left_at=(None if end_min is None
                     else base + timedelta(minutes=end_min)),
        )

    def _prior_no_shows(self, n):
        """Создаёт n прошлых no_show_student ученика (для исчерпания лимита прощения)."""
        for i in range(n):
            start = timezone.now() - timedelta(days=i + 1)
            sl = TimeSlot.objects.create(
                teacher=self.teacher, start_at=start,
                end_at=start + timedelta(minutes=60), status='booked',
            )
            Booking.objects.create(
                slot=sl, student=self.student, subject=self.subject,
                subscription=self.sub, status='no_show_student',
            )


# ───────────────────────── 1. Математика overlap ─────────────────────────


class OverlapComputationTests(_AttendBase):
    PREFIX = 'ovl'

    def setUp(self):
        super().setUp()
        self._window(lesson_min=60, end_ago_min=90)

    def test_no_sessions_all_zero(self):
        self.assertEqual(self.booking.compute_attendance(), (0, 0, 0))

    def test_full_presence_full_overlap(self):
        self._ses(ROLE_T, 0, 60)
        self._ses(ROLE_S, 0, 60)
        self.assertEqual(self.booking.compute_attendance(), (3600, 3600, 3600))

    def test_partial_overlap(self):
        self._ses(ROLE_T, 0, 40)
        self._ses(ROLE_S, 20, 60)
        t, s, o = self.booking.compute_attendance()
        self.assertEqual((t, s, o), (2400, 2400, 1200))  # overlap [20,40]=20мин

    def test_disjoint_intervals_zero_overlap(self):
        self._ses(ROLE_T, 0, 25)
        self._ses(ROLE_S, 35, 60)
        t, s, o = self.booking.compute_attendance()
        self.assertEqual((t, s, o), (1500, 1500, 0))

    def test_reconnect_overlapping_sessions_merged_no_double_count(self):
        # Учитель: [0,20] и [15,40] (пересекаются) → должно слиться в [0,40].
        self._ses(ROLE_T, 0, 20)
        self._ses(ROLE_T, 15, 40)
        self._ses(ROLE_S, 0, 40)
        t, s, o = self.booking.compute_attendance()
        self.assertEqual(t, 2400)   # 40 мин, а не 45
        self.assertEqual(o, 2400)

    def test_reconnect_with_gaps_sums_segments(self):
        self._ses(ROLE_T, 0, 10)
        self._ses(ROLE_T, 20, 30)
        self._ses(ROLE_T, 40, 55)
        t, _, _ = self.booking.compute_attendance()
        self.assertEqual(t, (10 + 10 + 15) * 60)  # 35 мин

    def test_nested_interval_overlap(self):
        self._ses(ROLE_T, 0, 60)
        self._ses(ROLE_S, 10, 20)
        t, s, o = self.booking.compute_attendance()
        self.assertEqual((t, s, o), (3600, 600, 600))

    def test_open_session_clamped_to_end_plus_grace(self):
        # Открытая сессия (не дошёл leave) клампится к end_at+grace(30мин).
        # Окно: start=now-150, end=now-90 → cap=end+30=now-60. duration=90мин.
        self._ses(ROLE_T, 0, None)
        t, _, _ = self.booking.compute_attendance()
        self.assertEqual(t, 90 * 60)  # 60 урок + 30 grace, не бесконечность

    def test_overlap_of_reconnects_on_both_sides(self):
        # Учитель [0,30]+[35,60], ученик [10,40]+[50,60].
        # overlap: [10,30]=20 + [35,40]=5 + [50,60]=10 = 35 мин.
        self._ses(ROLE_T, 0, 30)
        self._ses(ROLE_T, 35, 60)
        self._ses(ROLE_S, 10, 40)
        self._ses(ROLE_S, 50, 60)
        _, _, o = self.booking.compute_attendance()
        self.assertEqual(o, 35 * 60)


# ──────────────────── 2. Дерево решений settle_after_end ───────────────────


class SettleDecisionMatrixTests(_AttendBase):
    PREFIX = 'set'

    def setUp(self):
        super().setUp()
        self._window(lesson_min=60, end_ago_min=90)  # required = 24 мин

    def test_completed_when_all_three_above_threshold(self):
        self._ses(ROLE_T, 0, 50)
        self._ses(ROLE_S, 5, 55)
        self.assertEqual(self.booking.settle_after_end(), 'completed')
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, 'completed')
        self.assertIsNotNone(self.booking.ended_at)
        self.assertGreaterEqual(self.booking.overlap_duration_seconds, 24 * 60)
        self.assertGreaterEqual(self.booking.teacher_duration_seconds, 24 * 60)
        self.assertGreaterEqual(self.booking.student_duration_seconds, 24 * 60)

    def test_boundary_exactly_40_percent_is_completed(self):
        # Ровно 24 мин у всех (== required) → урок засчитан (>=).
        self._ses(ROLE_T, 0, 24)
        self._ses(ROLE_S, 0, 24)
        self.assertEqual(self.booking.settle_after_end(), 'completed')

    def test_teacher_just_below_threshold_is_no_show_teacher(self):
        self._ses(ROLE_T, 0, 23)   # 23 < 24
        self._ses(ROLE_S, 0, 60)
        self.assertEqual(self.booking.settle_after_end(), 'no_show_teacher')

    def test_student_just_below_threshold_is_no_show_student(self):
        self._ses(ROLE_T, 0, 60)
        self._ses(ROLE_S, 0, 23)
        self.assertEqual(self.booking.settle_after_end(), 'no_show_student')

    def test_both_below_threshold_is_not_held(self):
        self._ses(ROLE_T, 0, 10)
        self._ses(ROLE_S, 0, 10)
        self.assertEqual(self.booking.settle_after_end(), 'not_held')

    def test_both_present_but_low_overlap_is_not_held(self):
        # Анти-фрод §5: каждый ≥24мин, но НЕ одновременно → not_held.
        self._ses(ROLE_T, 0, 28)
        self._ses(ROLE_S, 32, 60)
        self.assertEqual(self.booking.settle_after_end(), 'not_held')
        self.assertTrue(
            LessonEvent.objects.filter(
                booking=self.booking, kind='settle_low_overlap').exists()
        )

    def test_short_join_fraud_is_not_held(self):
        # Зашли на минуту и вышли → урок не состоялся.
        self._ses(ROLE_T, 0, 1)
        self._ses(ROLE_S, 0, 1)
        self.assertEqual(self.booking.settle_after_end(), 'not_held')

    def test_one_full_one_short_is_no_show(self):
        # Учитель весь урок, ученик 3 мин → no_show_student (анти-фрод: не completed).
        self._ses(ROLE_T, 0, 60)
        self._ses(ROLE_S, 0, 3)
        self.assertEqual(self.booking.settle_after_end(), 'no_show_student')

    def test_external_link_completes_by_time_regardless_of_presence(self):
        # Внешняя ссылка (не Jitsi): присутствие не отслеживается → completed.
        self.booking.meeting_url = 'https://zoom.us/j/123456789'
        self.booking.save(update_fields=['meeting_url'])
        self.booking.refresh_from_db()
        self.assertFalse(self.booking.is_jitsi_meeting())
        self.assertEqual(self.booking.settle_after_end(), 'completed')

    def test_noop_when_not_confirmed(self):
        # Гонка: бронь уже отменена → settle ничего не делает.
        Booking.objects.filter(pk=self.booking.pk).update(status='cancelled_by_student')
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.settle_after_end(), 'cancelled_by_student')
        self.assertFalse(
            LessonAttendanceSession.objects.filter(booking=self.booking).exists())

    def test_forgiven_first_time_then_consumed_after_limit(self):
        # Первая неявка ученика прощается.
        self._ses(ROLE_T, 0, 60)
        self.assertEqual(self.booking.settle_after_end(), 'no_show_student')
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.no_show_forgiven)
        # При исчерпанном лимите (3 прежних) — засчитывается.
        self._prior_no_shows(3)
        b2 = self._fresh_jitsi_booking('set2')
        LessonAttendanceSession.objects.create(
            booking=b2, role=ROLE_T,
            joined_at=b2.slot.start_at, left_at=b2.slot.end_at)
        self.assertEqual(b2.settle_after_end(), 'no_show_student')
        b2.refresh_from_db()
        self.assertFalse(b2.no_show_forgiven)

    def _fresh_jitsi_booking(self, key):
        start = timezone.now() - timedelta(minutes=150)
        sl = TimeSlot.objects.create(
            teacher=self.teacher, start_at=start,
            end_at=start + timedelta(minutes=60), status='booked')
        b = Booking.objects.create(
            slot=sl, student=self.student, subject=self.subject,
            subscription=self.sub, status='confirmed')
        b.meeting_url = b.build_meeting_url()
        b.save(update_fields=['meeting_url'])
        return b


# ──────────────────── 3. record_join / record_leave ────────────────────────


class RecordJoinLeaveTests(_AttendBase):
    PREFIX = 'rec'

    def test_join_opens_session_and_sets_first_join(self):
        self.booking.record_join(is_teacher=True)
        self.booking.refresh_from_db()
        sessions = LessonAttendanceSession.objects.filter(
            booking=self.booking, role=ROLE_T)
        self.assertEqual(sessions.count(), 1)
        self.assertIsNone(sessions.first().left_at)       # ещё в комнате
        self.assertIsNotNone(self.booking.teacher_joined_at)
        self.assertIsNotNone(self.booking.started_at)

    def test_reconnect_closes_dangling_and_keeps_first_join(self):
        self.booking.record_join(is_teacher=True)
        self.booking.refresh_from_db()
        first_join = self.booking.teacher_joined_at
        # Второй join без leave (реконнект) → старая сессия закрыта, новая открыта.
        self.booking.record_join(is_teacher=True)
        self.booking.refresh_from_db()
        sessions = LessonAttendanceSession.objects.filter(
            booking=self.booking, role=ROLE_T).order_by('joined_at')
        self.assertEqual(sessions.count(), 2)
        self.assertIsNotNone(sessions[0].left_at)          # первая закрыта
        self.assertIsNone(sessions[1].left_at)             # вторая открыта
        self.assertEqual(self.booking.teacher_joined_at, first_join)  # не перезаписан

    def test_leave_closes_latest_open_and_sets_left_at(self):
        self.booking.record_join(is_teacher=False)
        self.booking.record_leave(is_teacher=False)
        self.booking.refresh_from_db()
        sess = LessonAttendanceSession.objects.filter(
            booking=self.booking, role=ROLE_S).first()
        self.assertIsNotNone(sess.left_at)
        self.assertIsNotNone(self.booking.student_left_at)

    def test_join_noop_on_non_confirmed(self):
        Booking.objects.filter(pk=self.booking.pk).update(status='completed')
        self.booking.refresh_from_db()
        self.booking.record_join(is_teacher=True)
        self.assertFalse(
            LessonAttendanceSession.objects.filter(booking=self.booking).exists())
        self.booking.refresh_from_db()
        self.assertIsNone(self.booking.teacher_joined_at)

    def test_leave_without_open_session_does_not_crash(self):
        # leave без предшествующего join — не должен падать.
        self.booking.record_leave(is_teacher=True)
        self.booking.refresh_from_db()
        self.assertIsNotNone(self.booking.teacher_left_at)
        self.assertEqual(
            LessonAttendanceSession.objects.filter(booking=self.booking).count(), 0)

    def test_two_full_cycles_create_two_closed_sessions(self):
        for _ in range(2):
            self.booking.record_join(is_teacher=True)
            self.booking.record_leave(is_teacher=True)
        sessions = LessonAttendanceSession.objects.filter(
            booking=self.booking, role=ROLE_T)
        self.assertEqual(sessions.count(), 2)
        self.assertTrue(all(s.left_at is not None for s in sessions))


# ──────────────────── 4. Последствия для денег ─────────────────────────────


class PayoutConsequenceTests(_AttendBase):
    PREFIX = 'pay'

    def setUp(self):
        super().setUp()
        # Урок закончился 7ч назад → попадает в окно sweeper (grace=6ч).
        self._window(lesson_min=60, end_ago_min=420)

    def test_completed_pays_teacher_after_grace_via_sweeper(self):
        self._ses(ROLE_T, 0, 55)
        self._ses(ROLE_S, 0, 55)
        self.assertEqual(self.booking.settle_after_end(), 'completed')

        res = release_pending_payouts()
        self.assertGreaterEqual(res['paid'], 1)

        self.teacher.user.wallet.refresh_from_db()
        self.platform.wallet.refresh_from_db()
        self.sub.refresh_from_db()
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('85000.00'))
        self.assertEqual(self.platform.wallet.balance, Decimal('15000.00'))
        self.assertEqual(self.sub.escrow_balance, Decimal('700000.00'))
        self.assertEqual(self.sub.lessons_paid_out, 1)

    def test_not_held_low_overlap_does_not_pay(self):
        self._ses(ROLE_T, 0, 28)
        self._ses(ROLE_S, 32, 60)
        self.assertEqual(self.booking.settle_after_end(), 'not_held')

        release_pending_payouts()  # not_held исключён из выборки sweeper
        self.teacher.user.wallet.refresh_from_db()
        self.sub.refresh_from_db()
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('0.00'))
        self.assertEqual(self.sub.escrow_balance, Decimal('800000.00'))
        self.assertEqual(self.sub.lessons_paid_out, 0)
        # Прямой вызов выплаты по not_held запрещён.
        with self.assertRaises(PayoutError):
            SubscriptionService.release_lesson_payout(self.booking)

    def test_no_show_forgiven_does_not_pay(self):
        self._ses(ROLE_T, 0, 60)  # учитель был, ученик — нет, первая неявка
        self.assertEqual(self.booking.settle_after_end(), 'no_show_student')
        self.booking.refresh_from_db()
        self.assertTrue(self.booking.no_show_forgiven)
        self.assertFalse(
            SubscriptionService.release_lesson_payout(self.booking))
        self.teacher.user.wallet.refresh_from_db()
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('0.00'))

    def test_no_show_consumed_pays_teacher(self):
        self._prior_no_shows(3)             # лимит прощения исчерпан
        self._ses(ROLE_T, 0, 60)            # учитель был, ученик — нет
        self.assertEqual(self.booking.settle_after_end(), 'no_show_student')
        self.booking.refresh_from_db()
        self.assertFalse(self.booking.no_show_forgiven)
        self.assertTrue(
            SubscriptionService.release_lesson_payout(self.booking))
        self.teacher.user.wallet.refresh_from_db()
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('85000.00'))

    def test_no_show_teacher_does_not_pay(self):
        self._ses(ROLE_S, 0, 60)  # ученик был, учитель — нет
        self.assertEqual(self.booking.settle_after_end(), 'no_show_teacher')
        release_pending_payouts()
        self.teacher.user.wallet.refresh_from_db()
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('0.00'))

    def test_payout_idempotent_no_double_pay(self):
        self._ses(ROLE_T, 0, 55)
        self._ses(ROLE_S, 0, 55)
        self.booking.settle_after_end()
        release_pending_payouts()
        release_pending_payouts()   # повторный прогон не должен заплатить дважды
        self.teacher.user.wallet.refresh_from_db()
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('85000.00'))
