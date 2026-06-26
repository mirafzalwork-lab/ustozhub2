"""
Тесты Фазы 2 · 2.6 — прочее P2.

- conversation_detail (no-JS POST) применяет те же антиспам-проверки, что AJAX;
- favorites_students получил действия «Написать» и «Убрать из избранного».
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model

from billing.tests import SIMPLE_STATIC_STORAGES
from teachers.models import (
    TeacherProfile, StudentProfile, City, Conversation, Message, FavoriteStudent,
)

User = get_user_model()
PWD = 'pass12345!'


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class Phase2MiscBase(TestCase):
    def setUp(self):
        self.city = City.objects.create(name='Ташкент')
        self.teacher_user = User.objects.create_user(
            username='m_t', password=PWD, email='mt@test.com',
            user_type='teacher', first_name='Иван', last_name='Петров')
        self.teacher = TeacherProfile.objects.create(
            user=self.teacher_user, city=self.city, experience_years=5,
            moderation_status='approved', is_active=True)
        self.student_user = User.objects.create_user(
            username='m_s', password=PWD, email='ms@test.com',
            user_type='student', first_name='Мария', last_name='Иванова')
        self.student = StudentProfile.objects.create(user=self.student_user, city=self.city)
        self.conv = Conversation.objects.create(
            teacher=self.teacher, student=self.student_user, is_active=True)


class NoJsAntispamTest(Phase2MiscBase):
    def test_post_blocked_when_rate_limited(self):
        self.client.login(username='m_s', password=PWD)
        before = Message.objects.filter(conversation=self.conv).count()
        with patch('teachers.consumers.message_rate_limited', return_value=True):
            self.client.post(reverse('conversation_detail', args=[self.conv.id]),
                             data={'content': 'спам спам спам'})
        # Сообщение не создано — лимит соблюдён и в no-JS ветке.
        self.assertEqual(Message.objects.filter(conversation=self.conv).count(), before)

    def test_post_allowed_when_not_limited(self):
        self.client.login(username='m_s', password=PWD)
        with patch('teachers.consumers.message_rate_limited', return_value=False):
            self.client.post(reverse('conversation_detail', args=[self.conv.id]),
                             data={'content': 'Здравствуйте!'})
        self.assertEqual(Message.objects.filter(conversation=self.conv).count(), 1)


class FavoriteStudentsActionsTest(Phase2MiscBase):
    def test_favorites_page_has_actions(self):
        FavoriteStudent.objects.create(teacher=self.teacher, student=self.student)
        self.client.login(username='m_t', password=PWD)
        r = self.client.get(reverse('my_favorite_students'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('fav-student-remove', html)              # кнопка удаления
        self.assertIn(reverse('start_conversation', args=[self.student_user.id]), html)  # «Написать»
        self.assertIn(reverse('toggle_favorite_student', args=[self.student.id]), html)
