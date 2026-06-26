"""
Тесты задачи 1.9 — регистрация ученика работает без JavaScript.

Раньше .reg-step были скрыты (display:none), класс is-active навешивался только
из JS, кнопки-переходы были type="button" → без JS форма пустая и неотправляемая.
Добавлен <noscript>-фолбэк, раскрывающий все шаги одной страницей. Поскольку все
шаги лежат в одном <form>, единый POST всех полей создаёт аккаунт.
"""
from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.cache import cache
from django.contrib.auth import get_user_model

from billing.tests import SIMPLE_STATIC_STORAGES
from teachers.models import City, Subject, SubjectCategory, StudentProfile

User = get_user_model()


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class RegisterNoscriptTest(TestCase):
    def setUp(self):
        cache.clear()  # сброс rate-limit между тестами
        self.city = City.objects.create(name='Ташкент')
        self.cat = SubjectCategory.objects.create(name='Языки')
        self.subject = Subject.objects.create(name='Английский', category=self.cat)

    def test_noscript_fallback_present(self):
        r = self.client.get(reverse('register_student'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        # Фолбэк раскрывает шаги и прячет прогресс/кнопки-переходы.
        self.assertIn('<noscript>', html)
        self.assertIn('.reg-step { display: block', html)
        self.assertIn('.reg-wizard-progress { display: none', html)

    def test_all_steps_in_single_form(self):
        # Без JS пользователь видит все шаги одной страницей и шлёт один POST —
        # значит все шаги и submit должны быть внутри одного <form>.
        html = self.client.get(reverse('register_student')).content.decode()
        form_start = html.index('id="registerForm"')
        form_end = html.index('</form>', form_start)
        chunk = html[form_start:form_end]
        self.assertIn('data-step="1"', chunk)
        self.assertIn('data-step="2"', chunk)
        self.assertIn('data-step="3"', chunk)
        self.assertIn('reg-nav__btn--submit', chunk)

    def test_single_post_creates_account(self):
        data = {
            'username': 'noscriptstud',
            'email': 'ns@test.com',
            'password1': 'Vortex!9241kp',
            'password2': 'Vortex!9241kp',
            'first_name': 'Тест',
            'last_name': 'Ученик',
            'phone': '+998901112299',
            'interests': [self.subject.id],
            'learning_format': 'both',
            'terms_accepted': 'on',
        }
        r = self.client.post(reverse('register_student'), data=data)
        self.assertEqual(r.status_code, 302)  # успех → редирект на подбор учителей
        user = User.objects.filter(username='noscriptstud').first()
        self.assertIsNotNone(user)
        self.assertEqual(user.user_type, 'student')
        profile = StudentProfile.objects.filter(user=user).first()
        self.assertIsNotNone(profile)
        self.assertIn(self.subject, profile.interests.all())
