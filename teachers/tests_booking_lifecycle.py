"""Тест-кейсы жизненного цикла бронирования и контроля доступа.

Покрывает пробелы существующего сьюта: state-machine брони (create_hold/
confirm/reject/expire/cancel), правила переноса и IDOR-доступ к уроку/комнате.
Хелперы переиспользуем из billing.tests.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.tests import (
    SIMPLE_STATIC_STORAGES,
    _make_student_with_balance,
    _make_teacher_with_subject,
)
from teachers.models import Booking, SlotUnavailable, TimeSlot


def _slot(teacher, *, in_hours=48, minutes=60, status='free'):
    start = timezone.now() + timedelta(hours=in_hours)
    return TimeSlot.objects.create(
        teacher=teacher, start_at=start,
        end_at=start + timedelta(minutes=minutes), status=status,
    )


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class BookingCreateHoldTests(TestCase):
    """create_hold: позитив + негатив + защита слота/учителя."""

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('bch_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('bch_s', balance=Decimal('0'))

    def test_hold_on_free_future_slot(self):
        slot = _slot(self.teacher)
        b = Booking.create_hold(slot_id=slot.id, student=self.student, subject=self.subject)
        self.assertEqual(b.status, 'pending')
        self.assertIsNotNone(b.expires_at)
        slot.refresh_from_db()
        self.assertEqual(slot.status, 'held')

    def test_hold_on_started_slot_rejected(self):
        start = timezone.now() - timedelta(minutes=5)
        slot = TimeSlot.objects.create(
            teacher=self.teacher, start_at=start,
            end_at=start + timedelta(minutes=60), status='free',
        )
        with self.assertRaises(SlotUnavailable):
            Booking.create_hold(slot_id=slot.id, student=self.student, subject=self.subject)

    def test_hold_on_busy_slot_rejected(self):
        slot = _slot(self.teacher, status='booked')
        with self.assertRaises(SlotUnavailable):
            Booking.create_hold(slot_id=slot.id, student=self.student, subject=self.subject)

    def test_second_hold_same_slot_rejected(self):
        slot = _slot(self.teacher)
        Booking.create_hold(slot_id=slot.id, student=self.student, subject=self.subject)
        s2 = _make_student_with_balance('bch_s2', balance=Decimal('0'))
        with self.assertRaises(SlotUnavailable):
            Booking.create_hold(slot_id=slot.id, student=s2, subject=self.subject)

    def test_hold_unapproved_teacher_rejected(self):
        self.teacher.moderation_status = 'pending'
        self.teacher.save()
        slot = _slot(self.teacher)
        with self.assertRaises(SlotUnavailable):
            Booking.create_hold(slot_id=slot.id, student=self.student, subject=self.subject)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class BookingTransitionTests(TestCase):
    """confirm / reject / expire / cancel — переходы и гонки."""

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('bt_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('bt_s', balance=Decimal('0'))

    def _pending(self):
        slot = _slot(self.teacher)
        return Booking.create_hold(slot_id=slot.id, student=self.student, subject=self.subject)

    def test_confirm_books_slot_and_creates_room(self):
        b = self._pending()
        b.confirm()
        b.refresh_from_db()
        self.assertEqual(b.status, 'confirmed')
        self.assertTrue(b.meeting_url)  # авто-Jitsi
        self.assertEqual(b.slot.status, 'booked')

    def test_confirm_twice_raises(self):
        b = self._pending()
        b.confirm()
        with self.assertRaises(ValueError):
            b.confirm()

    def test_reject_sets_cancelled_by_teacher_and_frees_slot(self):
        b = self._pending()
        b.reject(teacher_reply='нет времени')
        b.refresh_from_db()
        self.assertEqual(b.status, 'cancelled_by_teacher')
        self.assertEqual(b.slot.status, 'free')

    def test_expire_pending_frees_slot(self):
        b = self._pending()
        b.expire()
        b.refresh_from_db()
        self.assertEqual(b.status, 'expired')
        self.assertEqual(b.slot.status, 'free')

    def test_expire_confirmed_is_noop(self):
        # Гонка Celery expire ↔ уже подтверждённая бронь не должна откатить confirm.
        b = self._pending()
        b.confirm()
        b.expire()
        b.refresh_from_db()
        self.assertEqual(b.status, 'confirmed')

    def test_cancel_by_student_future_frees_slot(self):
        b = self._pending()
        b.confirm()
        b.cancel_by_student()
        b.refresh_from_db()
        self.assertEqual(b.status, 'cancelled_by_student')
        self.assertEqual(b.slot.status, 'free')

    def test_cancel_started_lesson_raises(self):
        b = self._pending()
        b.confirm()
        # «Перематываем» слот в прошлое — урок начался.
        b.slot.start_at = timezone.now() - timedelta(minutes=1)
        b.slot.save()
        with self.assertRaises(ValueError):
            b.cancel_by_student()


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class BookingRescheduleTests(TestCase):
    """Перенос разового/пробного урока учеником."""

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('br_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('br_s', balance=Decimal('0'))

    def _confirmed(self, in_hours=48):
        slot = _slot(self.teacher, in_hours=in_hours)
        b = Booking.create_hold(slot_id=slot.id, student=self.student, subject=self.subject)
        b.confirm()
        b.refresh_from_db()
        return b

    def test_reschedule_one_off_goes_back_to_pending(self):
        b = self._confirmed()
        new = _slot(self.teacher, in_hours=72)
        b.reschedule_by_student(new.id)
        b.refresh_from_db()
        self.assertEqual(b.status, 'pending')   # разовый → снова pending
        self.assertEqual(b.slot_id, new.id)
        new.refresh_from_db()
        self.assertEqual(new.status, 'held')

    def test_reschedule_too_close_rejected(self):
        b = self._confirmed(in_hours=2)  # < RESCHEDULE_MIN_LEAD_HOURS (4)
        new = _slot(self.teacher, in_hours=72)
        with self.assertRaises(ValueError):
            b.reschedule_by_student(new.id)

    def test_reschedule_to_same_slot_rejected(self):
        b = self._confirmed()
        with self.assertRaises(ValueError):
            b.reschedule_by_student(b.slot_id)

    def test_reschedule_to_busy_slot_rejected(self):
        b = self._confirmed()
        new = _slot(self.teacher, in_hours=72, status='booked')
        with self.assertRaises(SlotUnavailable):
            b.reschedule_by_student(new.id)

    def test_reschedule_to_other_teacher_slot_rejected(self):
        b = self._confirmed()
        other_teacher, _ = _make_teacher_with_subject('br_t2')
        foreign = _slot(other_teacher, in_hours=72)
        with self.assertRaises(ValueError):
            b.reschedule_by_student(foreign.id)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class BookingAccessControlTests(TestCase):
    """IDOR: посторонний не имеет доступа к чужому уроку/комнате/присутствию."""

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('ac_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('ac_s', balance=Decimal('0'))
        self.intruder = _make_student_with_balance('ac_x', balance=Decimal('0'))
        slot = _slot(self.teacher)
        self.booking = Booking.create_hold(
            slot_id=slot.id, student=self.student, subject=self.subject)
        self.booking.confirm()
        self.booking.refresh_from_db()

    def test_non_participant_cannot_open_lesson_room(self):
        self.client.login(username='ac_x', password='x' * 12)
        r = self.client.get(reverse('lesson_room', args=[self.booking.id]))
        self.assertEqual(r.status_code, 403)

    def test_non_participant_cannot_post_attendance(self):
        self.client.login(username='ac_x', password='x' * 12)
        r = self.client.post(
            reverse('lesson_attendance_api', args=[self.booking.id]),
            data={'event': 'join'},
        )
        self.assertEqual(r.status_code, 403)

    def test_participant_student_can_open_lesson_room(self):
        self.client.login(username='ac_s', password='x' * 12)
        r = self.client.get(reverse('lesson_room', args=[self.booking.id]))
        self.assertEqual(r.status_code, 200)

    def test_non_participant_cannot_report_teacher_noshow(self):
        self.client.login(username='ac_x', password='x' * 12)
        r = self.client.post(
            reverse('booking_report_teacher_noshow_api', args=[self.booking.id]))
        self.assertEqual(r.status_code, 403)
