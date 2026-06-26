"""
Тесты Фазы 2 · 2.1 — убраны «кнопки-обманки» в бронированиях.

- booking-dict отдаёт is_subscription, чтобы клиент показывал корректный текст
  переноса (подписочный урок переносится сразу, без подтверждения учителя);
- страница my_bookings содержит отдельный текст переноса для подписки.
(Удаление поля Meet/Zoom и кнопки set-link — клиентское, проверяется вживую.)
"""
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse

from billing.tests import (
    SIMPLE_STATIC_STORAGES,
    _make_student_with_balance,
    _make_teacher_with_subject,
)
from teachers.models import Booking
from teachers.tests_booking_lifecycle import _slot

PWD = 'x' * 12


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class IsSubscriptionFlagTest(TestCase):
    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('p2_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('p2_s', balance=Decimal('0'))
        slot = _slot(self.teacher)
        self.booking = Booking.create_hold(
            slot_id=slot.id, student=self.student, subject=self.subject)
        self.booking.confirm()
        self.booking.refresh_from_db()

    def test_api_exposes_is_subscription_false_for_oneoff(self):
        self.client.login(username='p2_s', password=PWD)
        r = self.client.get(reverse('my_bookings_api'))
        self.assertEqual(r.status_code, 200)
        items = r.json()['bookings']
        self.assertTrue(items)
        b = next(x for x in items if x['id'] == str(self.booking.id))
        self.assertIn('is_subscription', b)
        self.assertFalse(b['is_subscription'])  # разовая бронь


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class RescheduleHintTest(TestCase):
    def setUp(self):
        self.student = _make_student_with_balance('p2_hint', balance=Decimal('0'))

    def test_page_exposes_subscription_reschedule_hint(self):
        self.client.login(username='p2_hint', password=PWD)
        r = self.client.get(reverse('my_bookings_page'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        # Отдельный текст переноса для подписки (без ложного «учитель подтвердит»).
        self.assertIn('rescheduleHintSubscription', html)
        self.assertIn('rescheduledSubscription', html)

    def test_page_exposes_reschedule_lead_config(self):
        # Лид-тайм переноса прокинут на клиент, чтобы гасить кнопку заранее.
        self.client.login(username='p2_hint', password=PWD)
        html = self.client.get(reverse('my_bookings_page')).content.decode()
        self.assertIn('rescheduleMinLeadHours', html)
        self.assertIn('rescheduleTooLate', html)
