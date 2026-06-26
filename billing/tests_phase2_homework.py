"""
Тесты Фазы 2 · 2.5 — ДЗ: редактирование/удаление + напоминания о дедлайне.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import Homework
from billing.services import SubscriptionService
from billing.tasks import homework_due_reminders
from billing.tests import (
    SIMPLE_STATIC_STORAGES, _make_teacher_with_subject, _make_tariff,
    _make_student_with_balance,
)
from teachers.models import Notification

PWD = 'x' * 12


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class HomeworkPhase2Base(TestCase):
    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('hw25_t')
        self.tariff = _make_tariff(self.teacher, self.subject)
        self.student = _make_student_with_balance('hw25_s', balance=Decimal('1000000'))
        self.sub = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff, idempotency_key='hw25-buy')

    def _hw(self, status=Homework.Status.ASSIGNED, due_at=None):
        return Homework.objects.create(
            subscription=self.sub, teacher=self.teacher, student=self.student,
            title='Глава 1', description='Сделать упражнения.',
            status=status, due_at=due_at,
        )


class HomeworkEditDeleteTest(HomeworkPhase2Base):
    def test_edit_assigned_updates_title(self):
        hw = self._hw()
        self.client.login(username='hw25_t', password=PWD)
        r = self.client.post(reverse('teacher_homework_edit', args=[hw.id]), data={
            'title': 'Глава 1 (исправлено)', 'description': 'Новое описание.', 'due_at': '',
        })
        self.assertEqual(r.status_code, 302)
        hw.refresh_from_db()
        self.assertEqual(hw.title, 'Глава 1 (исправлено)')

    def test_edit_submitted_forbidden(self):
        hw = self._hw(status=Homework.Status.SUBMITTED)
        self.client.login(username='hw25_t', password=PWD)
        r = self.client.post(reverse('teacher_homework_edit', args=[hw.id]), data={
            'title': 'Нельзя', 'description': 'x', 'due_at': '',
        })
        self.assertEqual(r.status_code, 302)  # редирект с ошибкой
        hw.refresh_from_db()
        self.assertEqual(hw.title, 'Глава 1')  # не изменилось

    def test_delete_assigned(self):
        hw = self._hw()
        self.client.login(username='hw25_t', password=PWD)
        r = self.client.post(reverse('teacher_homework_delete', args=[hw.id]))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Homework.objects.filter(pk=hw.id).exists())

    def test_delete_submitted_forbidden(self):
        hw = self._hw(status=Homework.Status.SUBMITTED)
        self.client.login(username='hw25_t', password=PWD)
        r = self.client.post(reverse('teacher_homework_delete', args=[hw.id]))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Homework.objects.filter(pk=hw.id).exists())  # не удалено


class HomeworkDueReminderTest(HomeworkPhase2Base):
    def _reminder_notifs(self):
        return Notification.objects.filter(
            target_user=self.student, category='reminder')

    def test_reminder_sent_for_due_soon(self):
        hw = self._hw(due_at=timezone.now() + timedelta(hours=12))
        sent = homework_due_reminders()
        self.assertEqual(sent, 1)
        hw.refresh_from_db()
        self.assertIsNotNone(hw.reminder_sent_at)
        self.assertEqual(self._reminder_notifs().count(), 1)

    def test_reminder_idempotent(self):
        self._hw(due_at=timezone.now() + timedelta(hours=12))
        homework_due_reminders()
        homework_due_reminders()  # второй прогон не должен дублировать
        self.assertEqual(self._reminder_notifs().count(), 1)

    def test_reminder_skips_far_deadline(self):
        hw = self._hw(due_at=timezone.now() + timedelta(hours=48))
        sent = homework_due_reminders()
        self.assertEqual(sent, 0)
        hw.refresh_from_db()
        self.assertIsNone(hw.reminder_sent_at)

    def test_reminder_skips_no_deadline(self):
        self._hw(due_at=None)
        self.assertEqual(homework_due_reminders(), 0)

    def test_reminder_skips_submitted(self):
        self._hw(status=Homework.Status.SUBMITTED,
                 due_at=timezone.now() + timedelta(hours=12))
        self.assertEqual(homework_due_reminders(), 0)
