"""
Тесты Фазы 2 · 2.3 — не терять введённые данные при rate-limit.

login и register_student при срабатывании лимита раньше рендерили ПУСТУЮ форму
(пользователь терял введённое). Теперь возвращаем форму с данными запроса.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from billing.tests import SIMPLE_STATIC_STORAGES
from teachers.models import City, Subject, SubjectCategory


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class RateLimitKeepsInputTest(TestCase):
    def setUp(self):
        self.city = City.objects.create(name='Ташкент')
        self.cat = SubjectCategory.objects.create(name='Языки')
        self.subject = Subject.objects.create(name='Английский', category=self.cat)

    def test_login_ratelimit_keeps_username(self):
        with patch('django_ratelimit.core.is_ratelimited', return_value=True):
            r = self.client.post(reverse('login'), data={
                'username': 'keepme@test.com', 'password': 'whatever123',
            })
        self.assertEqual(r.status_code, 429)
        # Введённый логин сохранён в форме (пароль не сохраняется — это нормально).
        self.assertIn('keepme@test.com', r.content.decode())

    def test_register_ratelimit_keeps_data(self):
        with patch('django_ratelimit.core.is_ratelimited', return_value=True):
            r = self.client.post(reverse('register_student'), data={
                'username': 'keptuser', 'email': 'kept@test.com',
                'first_name': 'Сохранённое', 'last_name': 'Имя',
                'phone': '+998901112233',
                'password1': 'Vortex!9241kp', 'password2': 'Vortex!9241kp',
                'interests': [self.subject.id], 'learning_format': 'both',
                'terms_accepted': 'on',
            })
        self.assertEqual(r.status_code, 429)
        html = r.content.decode()
        self.assertIn('Сохранённое', html)   # имя не потеряно
        self.assertIn('keptuser', html)       # username не потерян
