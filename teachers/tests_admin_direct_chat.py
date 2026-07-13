"""
Тесты прямого чата «Поддержка ↔ пользователь» из admin-дашборда.

Админ находит ученика/учителя и пишет ему от «Поддержка UstozHub». Чат =
обычная Conversation (teacher=системный support-профиль, student=целевой
пользователь), поэтому виден и ученику, и учителю, и оба могут отвечать.
"""
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from teachers.models import (
    TeacherProfile, StudentProfile, City,
    Conversation, Message, get_support_profile,
)

User = get_user_model()


class AdminDirectChatTest(TestCase):
    def setUp(self):
        self.city = City.objects.create(name='Ташкент')
        self.admin = User.objects.create_user(
            username='adm', password='pass12345!', is_staff=True, user_type='student',
        )
        self.student = User.objects.create_user(
            username='stu', password='pass12345!', user_type='student',
            first_name='Мария', last_name='Иванова',
        )
        StudentProfile.objects.create(user=self.student, city=self.city)
        self.teacher_user = User.objects.create_user(
            username='tch', password='pass12345!', user_type='teacher',
            first_name='Иван', last_name='Петров',
        )
        TeacherProfile.objects.create(
            user=self.teacher_user, city=self.city, experience_years=3,
            moderation_status='approved', is_active=True,
        )
        self.support = get_support_profile()

    def _start_chat(self, target):
        c = Client(); c.force_login(self.admin)
        return c.post(reverse('admin_start_support_chat', kwargs={'user_id': target.id}))

    def test_support_account_exists(self):
        self.assertIsNotNone(self.support)
        self.assertEqual(self.support.user.get_full_name(), 'Поддержка UstozHub')

    def test_start_chat_creates_support_conversation(self):
        resp = self._start_chat(self.student)
        self.assertEqual(resp.status_code, 302)
        conv = Conversation.objects.get(teacher=self.support, student=self.student)
        self.assertTrue(conv.is_support)
        # Идемпотентно: повторный старт не плодит беседы.
        self._start_chat(self.student)
        self.assertEqual(
            Conversation.objects.filter(teacher=self.support, student=self.student).count(), 1)

    def test_admin_can_write_and_student_sees_and_replies(self):
        self._start_chat(self.student)
        conv = Conversation.objects.get(teacher=self.support, student=self.student)
        # Админ пишет через существующий support_reply.
        ca = Client(); ca.force_login(self.admin)
        ca.post(reverse('admin_conversation_detail', kwargs={'conversation_id': conv.id}),
                {'action': 'support_reply', 'content': 'Здравствуйте! Мы на связи.'})
        msg = Message.objects.get(conversation=conv)
        self.assertTrue(msg.is_admin_message)
        # Ученик видит чат в списке и открывает его.
        cs = Client(); cs.force_login(self.student)
        rlist = cs.get(reverse('conversations_list'))
        self.assertContains(rlist, 'Поддержка UstozHub')
        rdet = cs.get(reverse('conversation_detail', kwargs={'conversation_id': conv.id}))
        self.assertEqual(rdet.status_code, 200)
        self.assertContains(rdet, 'Мы на связи')
        # Ученик отвечает.
        cs.post(reverse('conversation_detail', kwargs={'conversation_id': conv.id}),
                {'content': 'Спасибо!'})
        self.assertTrue(Message.objects.filter(conversation=conv, sender=self.student).exists())

    def test_teacher_target_sees_and_replies(self):
        # Учитель в student-слоте support-чата — должен иметь доступ и ответить.
        self._start_chat(self.teacher_user)
        conv = Conversation.objects.get(teacher=self.support, student=self.teacher_user)
        # Админ пишет первым — иначе пустой чат не показывается в списке.
        ca = Client(); ca.force_login(self.admin)
        ca.post(reverse('admin_conversation_detail', kwargs={'conversation_id': conv.id}),
                {'action': 'support_reply', 'content': 'Добрый день, учитель!'})
        ct = Client(); ct.force_login(self.teacher_user)
        rlist = ct.get(reverse('conversations_list'))
        self.assertContains(rlist, 'Поддержка UstozHub')
        rdet = ct.get(reverse('conversation_detail', kwargs={'conversation_id': conv.id}))
        self.assertEqual(rdet.status_code, 200)
        ct.post(reverse('conversation_detail', kwargs={'conversation_id': conv.id}),
                {'content': 'Понял, спасибо'})
        self.assertTrue(Message.objects.filter(conversation=conv, sender=self.teacher_user).exists())

    def test_stranger_cannot_access(self):
        self._start_chat(self.student)
        conv = Conversation.objects.get(teacher=self.support, student=self.student)
        stranger = User.objects.create_user(username='x', password='p', user_type='student')
        StudentProfile.objects.create(user=stranger, city=self.city)
        cx = Client(); cx.force_login(stranger)
        rdet = cx.get(reverse('conversation_detail', kwargs={'conversation_id': conv.id}), follow=True)
        # Доступ закрыт: посторонний не видит содержимое переписки.
        self.assertNotContains(rdet, 'conversation_detail')
        # И через служебный доступ-хелпер (AJAX/WS путь) — 404.
        from teachers.views import _get_user_conversation
        from django.http import Http404
        with self.assertRaises(Http404):
            _get_user_conversation(stranger, conv.id)

    def test_search_finds_users(self):
        c = Client(); c.force_login(self.admin)
        r = c.get(reverse('admin_direct_messages'), {'q': 'Мария'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Мария')
        # Системный аккаунт не должен находиться в поиске.
        r2 = c.get(reverse('admin_direct_messages'), {'q': 'Поддержка'})
        self.assertNotContains(r2, 'admin_start_support_chat', status_code=200)
