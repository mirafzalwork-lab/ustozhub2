"""
Тесты функции «Ответ от Поддержки» (admin-дашборд).

Сценарий: ученик пишет учителю, учитель не отвечает. Админ из дашборда
отправляет сообщение от лица «Поддержка UstozHub» прямо в переписку. Оно:
  • помечается Message.is_admin_message=True;
  • уведомляет ОБОИХ участников (ученика и учителя) — real-time + Telegram;
  • не считается ни сообщением учителя, ни ответом ученика (не ломает
    анти-спам лида teacher_can_send_in_conversation / student_has_replied).
"""
from unittest.mock import patch

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from teachers.models import (
    TeacherProfile, StudentProfile, City, Subject, SubjectCategory,
    Conversation, Message, TelegramUser, NotificationQueue,
)
from teachers.leads import teacher_can_send_in_conversation, student_has_replied

User = get_user_model()


class AdminSupportMessageBase(TestCase):
    def setUp(self):
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

        self.admin_user = User.objects.create_user(
            username='admin1', password='pass12345!', email='admin@test.com',
            user_type='student', is_staff=True,
        )

        self.conv = Conversation.objects.create(
            teacher=self.teacher, student=self.student_user, is_active=True,
        )
        self.url = reverse('admin_conversation_detail', kwargs={'conversation_id': self.conv.pk})

    def _link_telegram(self, user, telegram_id):
        return TelegramUser.objects.create(
            user=user, telegram_id=telegram_id,
            notifications_enabled=True, started_bot=True,
        )

    def _support_msg(self, text='Извините за задержку, мы свяжемся с учителем.'):
        return Message.objects.create(
            conversation=self.conv, sender=self.admin_user,
            content=text, is_admin_message=True,
        )


class SupportSignalRoutingTest(AdminSupportMessageBase):
    """Сигнал: сообщение от поддержки уведомляет обоих участников."""

    def test_support_message_notifies_both_participants(self):
        with patch('teachers.consumers.notify_user') as mock_notify, \
             patch('teachers.context_processors.invalidate_message_cache'):
            self._support_msg()

        notified = {c.args[0] for c in mock_notify.call_args_list}
        self.assertEqual(notified, {self.student_user.pk, self.teacher_user.pk})
        for c in mock_notify.call_args_list:
            payload = c.args[2]
            self.assertEqual(c.args[1], 'new_message')
            self.assertTrue(payload['is_admin'])
            self.assertEqual(payload['sender_name'], 'Поддержка UstozHub')

    def test_support_message_queues_telegram_for_both(self):
        self._link_telegram(self.student_user, telegram_id=900101)
        self._link_telegram(self.teacher_user, telegram_id=900102)
        self._support_msg()
        self.assertEqual(
            NotificationQueue.objects.filter(recipient=self.student_user,
                                             notification_type='new_message').count(), 1)
        self.assertEqual(
            NotificationQueue.objects.filter(recipient=self.teacher_user,
                                             notification_type='new_message').count(), 1)


class SupportLeadGatingTest(AdminSupportMessageBase):
    """Админ-сообщение не влияет на анти-спам первого сообщения учителя."""

    def test_admin_message_is_not_student_reply(self):
        self._support_msg()
        self.assertFalse(student_has_replied(self.conv))

    def test_admin_message_does_not_unlock_locked_teacher(self):
        # Учитель отправил первое сообщение → заблокирован до ответа ученика.
        Message.objects.create(conversation=self.conv, sender=self.teacher_user,
                               content='Здравствуйте!')
        allowed, _r = teacher_can_send_in_conversation(self.conv)
        self.assertFalse(allowed)
        # Сообщение поддержки НЕ должно разблокировать учителя.
        self._support_msg()
        allowed, _r = teacher_can_send_in_conversation(self.conv)
        self.assertFalse(allowed)
        # Настоящий ответ ученика — разблокирует.
        Message.objects.create(conversation=self.conv, sender=self.student_user,
                               content='Добрый день!')
        allowed, _r = teacher_can_send_in_conversation(self.conv)
        self.assertTrue(allowed)


class SupportReplyViewTest(AdminSupportMessageBase):
    """View admin_conversation_detail: action=support_reply."""

    def test_staff_creates_flagged_support_message(self):
        c = Client()
        c.force_login(self.admin_user)
        resp = c.post(self.url, {'action': 'support_reply',
                                 'content': 'Мы уже связываемся с учителем.'})
        self.assertEqual(resp.status_code, 302)
        msg = Message.objects.get(conversation=self.conv)
        self.assertTrue(msg.is_admin_message)
        self.assertEqual(msg.sender, self.admin_user)

    def test_empty_content_creates_nothing(self):
        c = Client()
        c.force_login(self.admin_user)
        c.post(self.url, {'action': 'support_reply', 'content': '   '})
        self.assertFalse(Message.objects.filter(conversation=self.conv).exists())

    def test_non_staff_forbidden(self):
        c = Client()
        c.force_login(self.student_user)  # обычный ученик, не staff
        resp = c.post(self.url, {'action': 'support_reply', 'content': 'взлом'})
        # staff_member_required редиректит на логин админки.
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Message.objects.filter(conversation=self.conv).exists())
