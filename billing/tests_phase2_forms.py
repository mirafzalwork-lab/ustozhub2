"""
Тесты Фазы 2 · 2.2c — клиентские/серверные ограничения форм.

Здесь — серверная валидация дедлайна ДЗ (нельзя в прошлом). Клиентские min/max
(datetime-local min, max суммы пополнения) проверяются вживую/рендером.
"""
from datetime import timedelta

from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.forms import HomeworkForm
from billing.models import Homework
from billing.services import SubscriptionService
from billing.tests import (
    SIMPLE_STATIC_STORAGES, _make_teacher_with_subject, _make_tariff,
    _make_student_with_balance,
)


class HomeworkDueAtValidationTest(TestCase):
    def _form(self, due_at):
        return HomeworkForm(data={
            'title': 'Упражнения по грамматике',
            'description': 'Сделать стр. 10–12.',
            'due_at': due_at.strftime('%Y-%m-%dT%H:%M'),
        })

    def test_past_deadline_rejected(self):
        form = self._form(timezone.localtime(timezone.now()) - timedelta(days=1))
        self.assertFalse(form.is_valid())
        self.assertIn('due_at', form.errors)

    def test_future_deadline_accepted(self):
        form = self._form(timezone.localtime(timezone.now()) + timedelta(days=2))
        self.assertTrue(form.is_valid(), form.errors)

    def test_empty_deadline_ok(self):
        # Дедлайн опционален.
        form = HomeworkForm(data={
            'title': 'Без срока', 'description': 'Когда сможете.', 'due_at': '',
        })
        self.assertTrue(form.is_valid(), form.errors)

    def test_widget_has_min_attr(self):
        form = HomeworkForm()
        self.assertIn('min', form.fields['due_at'].widget.attrs)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class HomeworkSubmitNoDataLossTest(TestCase):
    """2.3 — пустая сдача ДЗ рендерит страницу заново (200), а не redirect (302),
    чтобы набранный ответ не терялся."""

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('p2hw_t')
        self.tariff = _make_tariff(self.teacher, self.subject)
        self.student = _make_student_with_balance('p2hw_s', balance=Decimal('1000000'))
        self.sub = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff, idempotency_key='p2hw-buy')
        self.hw = Homework.objects.create(
            subscription=self.sub, teacher=self.teacher, student=self.student,
            title='Глава 1', description='Прочитать и ответить.',
        )

    def test_empty_submit_rerenders_not_redirect(self):
        self.client.login(username='p2hw_s', password='x' * 12)
        r = self.client.post(reverse('homework_detail', args=[self.hw.id]),
                             data={'text_response': ''})
        self.assertEqual(r.status_code, 200)  # render, не 302 redirect
        self.hw.refresh_from_db()
        self.assertEqual(self.hw.status, Homework.Status.ASSIGNED)  # не сдано
