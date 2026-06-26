"""
Тесты задачи 1.5 — доставка уведомлений о новых сообщениях чата.

Контекст (после разбора кода): доставка УЖЕ работает через сигнал
teachers.signals.send_message_notification (post_save на Message):
  • real-time событие 'new_message' (тост + бейдж) — на каждое сообщение;
  • Telegram через queue_new_message_notification.

Реальный дефект был в Telegram-ветке: ключ идемпотентности не содержал
времени → был ВЕЧНЫМ, поэтому TG-уведомление о новом сообщении приходило
один раз на (отправитель, диалог) за всю историю и дальше молчало навсегда.

Исправление (решение продукта 2026-06-26): 5-минутный «бакет» в ключе —
внутри окна дедуп (не спамим), в новом окне ключ меняется (оффлайн-получатель
снова получает догон). Эти тесты фиксируют оба свойства плюс real-time-путь.
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse
from django.core.cache import cache
from django.utils import timezone
from django.contrib.auth import get_user_model

from teachers.models import (
    TeacherProfile, StudentProfile, City, Subject, SubjectCategory,
    Conversation, Message, TelegramUser, NotificationQueue,
)

User = get_user_model()


class MessageNotifyBase(TestCase):
    def setUp(self):
        cache.clear()
        self.city = City.objects.create(name='Ташкент')
        self.cat = SubjectCategory.objects.create(name='Языки')
        self.subject = Subject.objects.create(name='Английский', category=self.cat)

        self.teacher_user = User.objects.create_user(
            username='t1', password='pass12345!', email='t1@test.com',
            user_type='teacher', first_name='Иван', last_name='Петров',
        )
        self.teacher = TeacherProfile.objects.create(
            user=self.teacher_user, city=self.city, experience_years=5,
            moderation_status='approved', is_active=True,
        )
        self.student_user = User.objects.create_user(
            username='s1', password='pass12345!', email='s1@test.com',
            user_type='student', first_name='Мария', last_name='Иванова',
        )
        self.student = StudentProfile.objects.create(user=self.student_user, city=self.city)

        self.conv = Conversation.objects.create(
            teacher=self.teacher, student=self.student_user, is_active=True,
        )

    def _msg(self, sender, text='Привет, когда удобно начать?'):
        return Message.objects.create(conversation=self.conv, sender=sender, content=text)

    def _link_telegram(self, user, telegram_id=900001):
        return TelegramUser.objects.create(
            user=user, telegram_id=telegram_id,
            notifications_enabled=True, started_bot=True,
        )

    def _tg_queue_for(self, user):
        return NotificationQueue.objects.filter(
            recipient=user, notification_type='new_message',
        )


class RealtimeDeliveryTest(MessageNotifyBase):
    """Real-time путь (тост + бейдж) уже работает — фиксируем регрессией."""

    def test_new_message_event_pushed_to_recipient(self):
        # notify_user / invalidate_message_cache импортируются в сигнале локально,
        # поэтому патчим их по месту определения.
        with patch('teachers.consumers.notify_user') as mock_notify, \
             patch('teachers.context_processors.invalidate_message_cache') as mock_invalidate:
            self._msg(self.student_user)

        mock_notify.assert_called_once()
        args, _ = mock_notify.call_args
        self.assertEqual(args[0], self.teacher_user.pk)   # получатель = вторая сторона
        self.assertEqual(args[1], 'new_message')
        self.assertEqual(args[2]['conversation_id'], str(self.conv.pk))
        self.assertEqual(args[2]['sender_name'], 'Мария Иванова')
        mock_invalidate.assert_called_once_with(self.teacher_user.pk)

    def test_sender_does_not_notify_self(self):
        # Когда пишет учитель — событие уходит ученику, не учителю.
        with patch('teachers.consumers.notify_user') as mock_notify:
            self._msg(self.teacher_user, 'Здравствуйте, готов помочь')
        args, _ = mock_notify.call_args
        self.assertEqual(args[0], self.student_user.pk)


class TelegramDebounceTest(MessageNotifyBase):
    """Главное исправление: 5-минутное окно вместо вечного дедупа."""

    def test_deduped_within_same_window(self):
        self._link_telegram(self.teacher_user)
        fixed = timezone.now()
        with patch('telegram_bot.notification_service.timezone.now', return_value=fixed):
            self._msg(self.student_user, 'Первое')
            self._msg(self.student_user, 'Второе в том же окне')
        # Два сообщения в одном 5-мин окне → одно TG-уведомление.
        self.assertEqual(self._tg_queue_for(self.teacher_user).count(), 1)

    def test_resent_in_next_window(self):
        self._link_telegram(self.teacher_user)
        base = timezone.now()
        with patch('telegram_bot.notification_service.timezone.now', return_value=base):
            self._msg(self.student_user, 'Окно 1')
        # +301с — следующий 5-мин бакет: раньше тут было ВЕЧНОЕ молчание.
        with patch('telegram_bot.notification_service.timezone.now',
                   return_value=base + timedelta(seconds=301)):
            self._msg(self.student_user, 'Окно 2')
        self.assertEqual(self._tg_queue_for(self.teacher_user).count(), 2)

    def test_dedup_bucket_present_in_payload(self):
        self._link_telegram(self.teacher_user)
        self._msg(self.student_user)
        q = self._tg_queue_for(self.teacher_user).first()
        self.assertIsNotNone(q)
        self.assertIn('dedup_bucket', q.data)

    def test_no_queue_without_linked_bot(self):
        # Получатель без Telegram — запись не создаётся, ошибки нет.
        self._msg(self.student_user)
        self.assertEqual(self._tg_queue_for(self.teacher_user).count(), 0)
