"""
Тесты задачи 1.6 — видимость баннера «Подключить Telegram».

Раньше переменные telegram_connected / telegram_connect_url клались только во
вьюхах дашбордов, поэтому на странице уведомлений и в переписке баннер не
показывался. Теперь их даёт глобальный context-processor telegram_connect,
а баннер включён в notifications/list.html и conversations_list.html.
"""
from django.test import TestCase, Client, RequestFactory
from django.urls import reverse
from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser

from teachers.models import (
    TeacherProfile, StudentProfile, City, TelegramUser,
)
from teachers.context_processors import telegram_connect

User = get_user_model()


class TelegramConnectProcessorTest(TestCase):
    def setUp(self):
        cache.clear()
        self.rf = RequestFactory()
        self.city = City.objects.create(name='Ташкент')
        self.student_user = User.objects.create_user(
            username='s1', password='pass12345!', email='s1@test.com',
            user_type='student', first_name='Мария',
        )
        StudentProfile.objects.create(user=self.student_user, city=self.city)

    def _ctx(self, user):
        req = self.rf.get('/')
        req.user = user
        return telegram_connect(req)

    def test_anonymous_gets_empty(self):
        self.assertEqual(self._ctx(AnonymousUser()), {})

    def test_staff_gets_empty(self):
        admin = User.objects.create_user(
            username='adm', password='pass12345!', email='a@test.com', is_staff=True,
        )
        self.assertEqual(self._ctx(admin), {})

    def test_unlinked_user_gets_connect_url(self):
        ctx = self._ctx(self.student_user)
        self.assertFalse(ctx['telegram_connected'])
        self.assertTrue(ctx['telegram_connect_url'])  # непустой deep-link

    def test_linked_user_has_no_url(self):
        TelegramUser.objects.create(
            user=self.student_user, telegram_id=900100, started_bot=True,
        )
        ctx = self._ctx(self.student_user)
        self.assertTrue(ctx['telegram_connected'])
        self.assertEqual(ctx['telegram_connect_url'], '')

    def test_result_is_cached(self):
        self._ctx(self.student_user)
        self.assertIsNotNone(cache.get(f'tg_connect_ctx_{self.student_user.pk}'))


class BannerRenderTest(TestCase):
    def setUp(self):
        cache.clear()
        self.city = City.objects.create(name='Ташкент')
        self.student_user = User.objects.create_user(
            username='s1', password='pass12345!', email='s1@test.com',
            user_type='student', first_name='Мария',
        )
        StudentProfile.objects.create(user=self.student_user, city=self.city)
        self.client = Client()
        self.client.force_login(self.student_user)

    def test_banner_shown_on_notifications_for_unlinked(self):
        resp = self.client.get(reverse('notifications_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'tgConnectBanner')

    def test_banner_shown_on_conversations_for_unlinked(self):
        resp = self.client.get(reverse('conversations_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'tgConnectBanner')

    def test_banner_hidden_for_linked_user(self):
        TelegramUser.objects.create(
            user=self.student_user, telegram_id=900101, started_bot=True,
        )
        cache.clear()  # сбросить кэш статуса после привязки
        resp = self.client.get(reverse('notifications_list'))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'tgConnectBanner')
