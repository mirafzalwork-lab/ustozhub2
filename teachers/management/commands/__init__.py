"""
Django management command для управления Telegram пользователями
"""

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from teachers.models import TelegramUser
from telegram_bot.notifications import notification_service

User = get_user_model()


class Command(BaseCommand):
    help = 'Управление Telegram пользователями'

    def add_arguments(self, parser):
        parser.add_argument(
            '--action',
            type=str,
            choices=['list', 'link', 'test', 'unlink'],
            required=True,
            help='Действие: list, link, test, unlink'
        )
        parser.add_argument(
            '--telegram-id',
            type=int,
            help='Telegram ID пользователя'
        )
        parser.add_argument(
            '--username',
            type=str,
            help='Username Django пользователя'
        )
        parser.add_argument(
            '--email',
            type=str,
            help='Email Django пользователя'
        )

    def handle(self, *args, **options):
        action = options['action']

        if action == 'list':
            self.list_users()
        elif action == 'link':
            self.link_user(options)
        elif action == 'test':
            self.test_message(options)
        elif action == 'unlink':
            self.unlink_user(options)

    def list_users(self):
        """Показать список всех пользователей"""
        self.stdout.write("📋 СПИСОК ПОЛЬЗОВАТЕЛЕЙ")
        self.stdout.write("=" * 60)
        
        # Django пользователи
        django_users = User.objects.all()
        self.stdout.write(f"\n👥 Django пользователи ({django_users.count()}):")
        for user in django_users:
            tg_user = getattr(user, 'telegram_user', None)
            status = "🔗 Привязан" if tg_user else "❌ Не привязан"
            self.stdout.write(f"  • {user.username} ({user.email}) - {status}")
        
        # Telegram пользователи
        tg_users = TelegramUser.objects.all()
        self.stdout.write(f"\n📱 Telegram пользователи ({tg_users.count()}):")
        for tg_user in tg_users:
            status = "🔗 Привязан" if tg_user.user else "❌ Не привязан"
            ready = "✅ Готов" if tg_user.notifications_enabled and tg_user.started_bot else "⚠️ Не готов"
            self.stdout.write(f"  • {tg_user.first_name} (@{tg_user.telegram_username or 'нет'}) - {status} - {ready}")

    def link_user(self, options):
        """Привязать Telegram пользователя к Django пользователю"""
        telegram_id = options.get('telegram_id')
        username = options.get('username')
        email = options.get('email')

        if not telegram_id:
            raise CommandError('Необходимо указать --telegram-id')

        if not username and not email:
            raise CommandError('Необходимо указать --username или --email')

        # Находим Telegram пользователя
        try:
            tg_user = TelegramUser.objects.get(telegram_id=telegram_id)
        except TelegramUser.DoesNotExist:
            raise CommandError(f'Telegram пользователь с ID {telegram_id} не найден')

        # Находим Django пользователя
        try:
            if username:
                django_user = User.objects.get(username=username)
            else:
                django_user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise CommandError(f'Django пользователь не найден')

        # Привязываем
        tg_user.user = django_user
        tg_user.save()

        self.stdout.write(
            self.style.SUCCESS(
                f'✅ Успешно привязан: {tg_user.first_name} (@{tg_user.telegram_username}) -> {django_user.username}'
            )
        )

    def test_message(self, options):
        """Отправить тестовое сообщение"""
        telegram_id = options.get('telegram_id')
        username = options.get('username')
        email = options.get('email')

        if telegram_id:
            # Отправляем по Telegram ID
            try:
                tg_user = TelegramUser.objects.get(telegram_id=telegram_id)
                success = notification_service.send_message_sync(
                    telegram_id=telegram_id,
                    text="🧪 Тестовое сообщение от системы управления"
                )
                if success:
                    self.stdout.write(
                        self.style.SUCCESS(f'✅ Сообщение отправлено пользователю {tg_user.first_name}')
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(f'❌ Ошибка отправки сообщения')
                    )
            except TelegramUser.DoesNotExist:
                raise CommandError(f'Telegram пользователь с ID {telegram_id} не найден')
        else:
            # Отправляем через Django пользователя
            if not username and not email:
                raise CommandError('Необходимо указать --telegram-id, --username или --email')

            try:
                if username:
                    django_user = User.objects.get(username=username)
                else:
                    django_user = User.objects.get(email=email)
            except User.DoesNotExist:
                raise CommandError(f'Django пользователь не найден')

            # Используем функцию уведомлений
            from telegram_bot.notifications import send_telegram_notification
            success = send_telegram_notification(
                user=django_user,
                sender_name="Система управления",
                message_preview="Тестовое сообщение"
            )

            if success:
                self.stdout.write(
                    self.style.SUCCESS(f'✅ Уведомление отправлено пользователю {django_user.username}')
                )
            else:
                self.stdout.write(
                    self.style.ERROR(f'❌ Ошибка отправки уведомления')
                )

    def unlink_user(self, options):
        """Отвязать Telegram пользователя от Django пользователя"""
        telegram_id = options.get('telegram_id')

        if not telegram_id:
            raise CommandError('Необходимо указать --telegram-id')

        try:
            tg_user = TelegramUser.objects.get(telegram_id=telegram_id)
            old_user = tg_user.user
            tg_user.user = None
            tg_user.save()

            self.stdout.write(
                self.style.SUCCESS(
                    f'✅ Отвязан: {tg_user.first_name} (@{tg_user.telegram_username}) от {old_user.username if old_user else "неизвестного пользователя"}'
                )
            )
        except TelegramUser.DoesNotExist:
            raise CommandError(f'Telegram пользователь с ID {telegram_id} не найден')

