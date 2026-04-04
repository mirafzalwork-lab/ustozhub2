"""
Тесты для исправлений Telegram-бота и админ-панели.
"""
import json
from unittest.mock import patch, MagicMock, AsyncMock
from django.test import TestCase, Client, RequestFactory, override_settings
from django.urls import reverse, resolve
from django.contrib.auth import get_user_model

from teachers.models import (
    TelegramUser, TeacherProfile, StudentProfile,
    City, Subject, SubjectCategory,
)

User = get_user_model()


class BaseTestCase(TestCase):
    """Базовый класс с общими fixtures."""

    def setUp(self):
        # Админ
        self.admin = User.objects.create_superuser(
            username='admin', password='testpass123!', email='admin@test.com',
            user_type='teacher',
        )
        # Обычный учитель
        self.teacher_user = User.objects.create_user(
            username='teacher1', password='testpass123!', email='teacher@test.com',
            user_type='teacher', first_name='Иван', last_name='Петров',
        )
        # Обычный ученик
        self.student_user = User.objects.create_user(
            username='student1', password='testpass123!', email='student@test.com',
            user_type='student', first_name='Мария', last_name='Иванова',
        )
        # Профили
        self.city = City.objects.create(name='Ташкент')
        self.teacher_profile = TeacherProfile.objects.create(
            user=self.teacher_user, city=self.city, experience_years=5,
            moderation_status='approved', is_active=True,
        )
        self.student_profile = StudentProfile.objects.create(
            user=self.student_user, city=self.city,
        )
        # Telegram-пользователь (привязан к учителю)
        self.tg_user = TelegramUser.objects.create(
            user=self.teacher_user,
            telegram_id=123456789,
            telegram_username='teacher_tg',
            first_name='Иван',
            last_name='Петров',
            started_bot=True,
            notifications_enabled=True,
        )
        self.client = Client()


# =====================================================================
# 1. Тест: toggle notifications API (исправление #5)
# =====================================================================

class AdminToggleNotificationsTest(BaseTestCase):
    """Тест API переключения уведомлений Telegram-пользователя."""

    def test_toggle_off(self):
        """Уведомления включены → выключаются."""
        self.client.login(username='admin', password='testpass123!')
        self.assertTrue(self.tg_user.notifications_enabled)

        url = reverse('admin_toggle_telegram_notifications', args=[self.tg_user.id])
        resp = self.client.post(url, content_type='application/json')

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertFalse(data['notifications_enabled'])

        self.tg_user.refresh_from_db()
        self.assertFalse(self.tg_user.notifications_enabled)

    def test_toggle_on(self):
        """Уведомления выключены → включаются."""
        self.tg_user.notifications_enabled = False
        self.tg_user.save()

        self.client.login(username='admin', password='testpass123!')

        url = reverse('admin_toggle_telegram_notifications', args=[self.tg_user.id])
        resp = self.client.post(url, content_type='application/json')

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['notifications_enabled'])

        self.tg_user.refresh_from_db()
        self.assertTrue(self.tg_user.notifications_enabled)

    def test_requires_staff(self):
        """Обычный пользователь не может переключать уведомления."""
        self.client.login(username='teacher1', password='testpass123!')
        url = reverse('admin_toggle_telegram_notifications', args=[self.tg_user.id])
        resp = self.client.post(url, content_type='application/json')
        # staff_member_required редиректит на admin login
        self.assertNotEqual(resp.status_code, 200)

    def test_requires_post(self):
        """GET запрос отклоняется."""
        self.client.login(username='admin', password='testpass123!')
        url = reverse('admin_toggle_telegram_notifications', args=[self.tg_user.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 405)

    def test_nonexistent_user(self):
        """Несуществующий пользователь — 404."""
        self.client.login(username='admin', password='testpass123!')
        url = reverse('admin_toggle_telegram_notifications', args=[99999])
        resp = self.client.post(url, content_type='application/json')
        self.assertEqual(resp.status_code, 404)


# =====================================================================
# 2. Тест: broadcast message (отправка всем/учителям/ученикам)
# =====================================================================

class BroadcastMessageTest(BaseTestCase):
    """Тест массовой рассылки через админку."""

    def test_broadcast_requires_staff(self):
        self.client.login(username='student1', password='testpass123!')
        resp = self.client.post(reverse('send_broadcast_message'), {'message': 'test', 'recipients': 'all'})
        self.assertNotEqual(resp.status_code, 200)  # redirect to login

    def test_broadcast_empty_message(self):
        self.client.login(username='admin', password='testpass123!')
        resp = self.client.post(reverse('send_broadcast_message'), {'message': '', 'recipients': 'all'})
        self.assertEqual(resp.status_code, 302)  # redirect back

    @patch('teachers.admin_telegram_service.admin_telegram_service')
    def test_broadcast_to_all(self, mock_service):
        mock_service.send_to_selected_users.return_value = {'success': 1, 'failed': 0, 'total': 1, 'details': []}
        self.client.login(username='admin', password='testpass123!')

        resp = self.client.post(reverse('send_broadcast_message'), {
            'message': 'Тестовая рассылка',
            'recipients': 'all',
        })
        self.assertEqual(resp.status_code, 302)
        mock_service.send_to_selected_users.assert_called_once()

    @patch('teachers.admin_telegram_service.admin_telegram_service')
    def test_broadcast_to_teachers(self, mock_service):
        mock_service.send_to_selected_users.return_value = {'success': 1, 'failed': 0, 'total': 1, 'details': []}
        self.client.login(username='admin', password='testpass123!')

        resp = self.client.post(reverse('send_broadcast_message'), {
            'message': 'Для учителей',
            'recipients': 'teachers',
        })
        self.assertEqual(resp.status_code, 302)
        mock_service.send_to_selected_users.assert_called_once()
        # Проверяем что фильтр по учителям был применён
        users_arg = mock_service.send_to_selected_users.call_args[1].get('telegram_users') or \
                     mock_service.send_to_selected_users.call_args[0][0]
        for u in users_arg:
            self.assertEqual(u.user.user_type, 'teacher')


# =====================================================================
# 3. Тест: individual message
# =====================================================================

class IndividualMessageTest(BaseTestCase):
    """Тест отправки персонального сообщения."""

    @patch('teachers.admin_telegram_service.admin_telegram_service')
    def test_send_individual_success(self, mock_service):
        mock_service.send_message_sync.return_value = True
        self.client.login(username='admin', password='testpass123!')

        resp = self.client.post(reverse('send_individual_message'), {
            'user_id': self.tg_user.id,
            'message': 'Персональное сообщение',
        })
        self.assertEqual(resp.status_code, 302)
        mock_service.send_message_sync.assert_called_once()

    @patch('teachers.admin_telegram_service.admin_telegram_service')
    def test_send_individual_to_inactive_user(self, mock_service):
        """Отправка пользователю, который не активировал бота."""
        self.tg_user.started_bot = False
        self.tg_user.save()

        self.client.login(username='admin', password='testpass123!')
        resp = self.client.post(reverse('send_individual_message'), {
            'user_id': self.tg_user.id,
            'message': 'Тест',
        })
        self.assertEqual(resp.status_code, 302)
        # Не должен вызывать send, т.к. бот не активирован
        mock_service.send_message_sync.assert_not_called()

    def test_send_individual_empty_message(self):
        self.client.login(username='admin', password='testpass123!')
        resp = self.client.post(reverse('send_individual_message'), {
            'user_id': self.tg_user.id,
            'message': '',
        })
        self.assertEqual(resp.status_code, 302)


# =====================================================================
# 4. Тест: telegram management page
# =====================================================================

@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class TelegramManagementPageTest(BaseTestCase):
    """Тест страницы управления Telegram."""

    def test_page_loads_for_admin(self):
        self.client.login(username='admin', password='testpass123!')
        resp = self.client.get(reverse('telegram_management'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Управление Telegram')
        self.assertContains(resp, self.tg_user.first_name)

    def test_page_forbidden_for_regular_user(self):
        self.client.login(username='student1', password='testpass123!')
        resp = self.client.get(reverse('telegram_management'))
        self.assertNotEqual(resp.status_code, 200)

    def test_stats_in_context(self):
        self.client.login(username='admin', password='testpass123!')
        resp = self.client.get(reverse('telegram_management'))
        self.assertIn('stats', resp.context)
        stats = resp.context['stats']
        self.assertEqual(stats['total_users'], 1)
        self.assertEqual(stats['active_users'], 1)
        self.assertEqual(stats['notifications_enabled'], 1)


# =====================================================================
# 5. Тест: URL уведомлений (исправление #2 — /messages/ вместо /conversations/)
# =====================================================================

class NotificationServiceURLTest(TestCase):
    """Тест: URL в Telegram-уведомлениях использует /messages/."""

    def test_conversation_url_uses_messages_path(self):
        from telegram_bot.notification_service import TelegramNotificationService
        service = TelegramNotificationService()

        # Создаём пользователя + TelegramUser для прохождения проверки
        user = User.objects.create_user(
            username='urltest', password='testpass123!', user_type='student',
        )
        TelegramUser.objects.create(
            user=user, telegram_id=111222333,
            started_bot=True, notifications_enabled=True,
        )

        notification = service.notify_new_message(
            recipient=user,
            sender_name='Тест',
            message_preview='Привет',
            conversation_id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        )

        if notification:
            self.assertIn('/messages/', notification.data.get('url', ''))
            self.assertNotIn('/conversations/', notification.data.get('url', ''))


# =====================================================================
# 6. Тест: Bot callback handler (исправление #1)
# =====================================================================

class BotCallbackTest(TestCase):
    """Тест: callback handler для settings/toggle_notifications не падает."""

    def test_handle_callback_settings_uses_edit_message(self):
        """Проверяем что settings callback использует query.edit_message_text, а не update.message."""
        import ast
        with open('telegram_bot/bot.py', 'r') as f:
            source = f.read()
        tree = ast.parse(source)

        # Находим handle_callback
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == 'handle_callback':
                body_source = ast.get_source_segment(source, node)
                # Убеждаемся что НЕ вызывается notifications_command из callback
                self.assertNotIn('notifications_command(update', body_source)
                # Убеждаемся что используется query.edit_message_text
                self.assertIn('query.edit_message_text', body_source)
                break
        else:
            self.fail('handle_callback function not found in bot.py')


# =====================================================================
# 7. Тест: URL routing
# =====================================================================

class URLRoutingTest(TestCase):
    """Тест: все новые URL маршруты резолвятся."""

    def test_toggle_notifications_url_resolves(self):
        url = reverse('admin_toggle_telegram_notifications', args=[1])
        self.assertEqual(resolve(url).url_name, 'admin_toggle_telegram_notifications')

    def test_broadcast_url_resolves(self):
        url = reverse('send_broadcast_message')
        self.assertEqual(resolve(url).url_name, 'send_broadcast_message')

    def test_individual_message_url_resolves(self):
        url = reverse('send_individual_message')
        self.assertEqual(resolve(url).url_name, 'send_individual_message')

    def test_telegram_management_url_resolves(self):
        url = reverse('telegram_management')
        self.assertEqual(resolve(url).url_name, 'telegram_management')

    def test_export_url_resolves(self):
        url = reverse('export_telegram_users')
        self.assertEqual(resolve(url).url_name, 'export_telegram_users')


# =====================================================================
# 8. Тест: export CSV
# =====================================================================

class ExportCSVTest(BaseTestCase):
    """Тест экспорта Telegram-пользователей в CSV."""

    def test_export_returns_csv(self):
        self.client.login(username='admin', password='testpass123!')
        resp = self.client.get(reverse('export_telegram_users'))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/csv', resp['Content-Type'])
        self.assertIn('attachment', resp['Content-Disposition'])
        content = resp.content.decode('utf-8-sig')
        self.assertIn('Telegram ID', content)
        self.assertIn('123456789', content)
