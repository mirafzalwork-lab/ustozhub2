"""
Тесты задачи 1.7 (комната) — кнопка «Преподаватель не пришёл» без перезагрузки.

Раньше право на репорт (`can_report_teacher_noshow`) вычислялось только на
сервере при рендере и зависело от времени → вошедший вовремя ученик кнопку не
видел никогда без ручного reload. Теперь сервер отдаёт право, НЕ зависящее от
времени (`noshow_eligible`) + момент `noshow_report_at`, а клиентский таймер
раскрывает блок без перезагрузки. Здесь проверяем серверный контракт и рендер.
"""
from datetime import timedelta

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.tests import (
    SIMPLE_STATIC_STORAGES,
    _make_student_with_balance,
    _make_teacher_with_subject,
)
from teachers.models import Booking, TimeSlot

PWD = 'x' * 12


def _slot_at(teacher, start, *, minutes=60, status='free'):
    return TimeSlot.objects.create(
        teacher=teacher, start_at=start,
        end_at=start + timedelta(minutes=minutes), status=status,
    )


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class NoshowContextTest(TestCase):
    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('ac_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('ac_s', balance=0)
        # Слот в будущем (комната ещё закрыта) — контекст всё равно считается.
        slot = _slot_at(self.teacher, timezone.now() + timedelta(hours=48))
        self.booking = Booking.create_hold(
            slot_id=slot.id, student=self.student, subject=self.subject)
        self.booking.confirm()
        self.booking.refresh_from_db()

    def test_student_is_eligible(self):
        self.client.login(username='ac_s', password=PWD)
        r = self.client.get(reverse('lesson_room', args=[self.booking.id]))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context['noshow_eligible'])

    def test_report_at_is_start_plus_grace(self):
        self.client.login(username='ac_s', password=PWD)
        r = self.client.get(reverse('lesson_room', args=[self.booking.id]))
        grace = getattr(settings, 'TEACHER_NO_SHOW_REPORT_AFTER_MINUTES', 15)
        expected = self.booking.slot.start_at + timedelta(minutes=grace)
        self.assertEqual(r.context['noshow_report_at'], expected)

    def test_teacher_is_not_eligible(self):
        self.client.login(username='ac_t', password=PWD)
        r = self.client.get(reverse('lesson_room', args=[self.booking.id]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.context['noshow_eligible'])


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class NoshowRenderTest(TestCase):
    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('rn_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('rn_s', balance=0)
        # Слот стартует через ~6 минут → окно входа уже открыто (start−10мин),
        # комната доступна (can_join=True), значит блок неявки попадает в DOM.
        slot = _slot_at(self.teacher, timezone.now() + timedelta(minutes=6))
        self.booking = Booking.create_hold(
            slot_id=slot.id, student=self.student, subject=self.subject)
        self.booking.confirm()
        self.booking.refresh_from_db()

    def test_block_present_but_hidden_with_data_attrs(self):
        self.client.login(username='rn_s', password=PWD)
        r = self.client.get(reverse('lesson_room', args=[self.booking.id]))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.context['can_join'])  # комната открыта
        html = r.content.decode()
        # Блок отрендерен (скрыт), с данными для клиентского таймера/запроса.
        self.assertIn('id="lr-noshow"', html)
        self.assertIn('data-report-at=', html)
        self.assertIn('data-report-url=', html)
        self.assertIn('id="lr-report-noshow"', html)

    def test_block_absent_for_teacher(self):
        self.client.login(username='rn_t', password=PWD)
        r = self.client.get(reverse('lesson_room', args=[self.booking.id]))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('id="lr-noshow"', r.content.decode())


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class NoshowFromListTest(TestCase):
    """1.7b — отметка неявки прямо из списка уроков. Сервер — арбитр (валидация
    времени/присутствия в student_report_teacher_no_show)."""

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('ls_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('ls_s', balance=0)
        self.grace = getattr(settings, 'TEACHER_NO_SHOW_REPORT_AFTER_MINUTES', 15)

    def _confirmed_booking_started(self, minutes_ago):
        # Создаём в будущем (обходим валидацию create_hold), затем сдвигаем слот
        # в прошлое на нужный момент начала урока.
        slot = _slot_at(self.teacher, timezone.now() + timedelta(hours=48))
        b = Booking.create_hold(slot_id=slot.id, student=self.student, subject=self.subject)
        b.confirm()
        b.refresh_from_db()
        s = b.slot
        s.start_at = timezone.now() - timedelta(minutes=minutes_ago)
        s.end_at = s.start_at + timedelta(minutes=60)
        s.save(update_fields=['start_at', 'end_at'])
        b.refresh_from_db()
        return b

    def test_report_too_early_returns_409(self):
        b = self._confirmed_booking_started(minutes_ago=self.grace - 10)  # ещё рано
        self.client.login(username='ls_s', password=PWD)
        r = self.client.post(reverse('booking_report_teacher_noshow_api', args=[b.id]))
        self.assertEqual(r.status_code, 409)
        b.refresh_from_db()
        self.assertEqual(b.status, 'confirmed')  # статус не изменился

    def test_report_success_after_grace(self):
        b = self._confirmed_booking_started(minutes_ago=self.grace + 5)
        self.client.login(username='ls_s', password=PWD)
        r = self.client.post(reverse('booking_report_teacher_noshow_api', args=[b.id]))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get('ok'))
        b.refresh_from_db()
        self.assertEqual(b.status, 'no_show_teacher')

    def test_report_blocked_if_teacher_joined(self):
        b = self._confirmed_booking_started(minutes_ago=self.grace + 5)
        b.teacher_joined_at = timezone.now()
        b.save(update_fields=['teacher_joined_at'])
        self.client.login(username='ls_s', password=PWD)
        r = self.client.post(reverse('booking_report_teacher_noshow_api', args=[b.id]))
        self.assertEqual(r.status_code, 409)
        b.refresh_from_db()
        self.assertEqual(b.status, 'confirmed')

    def test_my_bookings_page_exposes_noshow_config(self):
        # Шаблон должен отдавать url и порог для клиентской кнопки.
        self.client.login(username='ls_s', password=PWD)
        r = self.client.get(reverse('my_bookings_page'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('reportNoshow', html)
        self.assertIn('report-teacher-noshow', html)
        self.assertIn('noShowReportMinutes', html)
