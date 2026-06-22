"""Живая проверка доставки Telegram-уведомлений конкретному пользователю.

Прогоняет реальный прод-путь: ставит уведомление в NotificationQueue и
(с --flush) сразу отправляет через тот же код, что и демон telegram-bot.service.

Примеры:
    python manage.py telegram_selftest --user olamgir --flush
    python manage.py telegram_selftest --user 42
    python manage.py telegram_selftest --user @durov --flush
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

User = get_user_model()


class Command(BaseCommand):
    help = 'Отправляет тестовое Telegram-уведомление пользователю и проверяет всю цепочку.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user', required=True,
            help='username, email, id пользователя сайта или @telegram_username',
        )
        parser.add_argument(
            '--flush', action='store_true',
            help='сразу отправить (не ждать демон очереди)',
        )

    def _resolve_user(self, ident):
        from teachers.models import TelegramUser
        # @tg_username → ищем по привязанному TelegramUser
        if ident.startswith('@'):
            tg = TelegramUser.objects.filter(
                telegram_username__iexact=ident.lstrip('@')
            ).select_related('user').first()
            if tg and tg.user:
                return tg.user
            raise CommandError(f'Нет привязанного пользователя для {ident}')
        # числовой id
        if ident.isdigit():
            u = User.objects.filter(pk=int(ident)).first()
            if u:
                return u
        # username / email
        u = User.objects.filter(
            Q(username__iexact=ident) | Q(email__iexact=ident)
        ).first()
        if not u:
            raise CommandError(f'Пользователь не найден: {ident}')
        return u

    def handle(self, *args, **opts):
        from django.conf import settings
        from teachers.models import TelegramUser
        from telegram_bot.notification_service import (
            queue_user_notification, process_notification_queue,
        )

        # 0. Токен
        if not getattr(settings, 'TELEGRAM_BOT_TOKEN', ''):
            raise CommandError('TELEGRAM_BOT_TOKEN не задан — отправка невозможна.')

        user = self._resolve_user(opts['user'])
        self.stdout.write(f'Пользователь: {user.pk} {user.username} ({user.email})')

        # 1. Диагностика привязки
        tg = TelegramUser.objects.filter(user=user).first()
        if not tg:
            raise CommandError(
                'У пользователя НЕТ привязанного Telegram. Пусть откроет баннер '
                '«Подключить» на дашборде и нажмёт Start в боте.'
            )
        self.stdout.write(
            f'TelegramUser: telegram_id={tg.telegram_id} '
            f'started_bot={tg.started_bot} notifications_enabled={tg.notifications_enabled}'
        )
        if not tg.started_bot:
            raise CommandError('started_bot=False — пользователь не нажал Start в боте.')
        if not tg.notifications_enabled:
            raise CommandError('notifications_enabled=False — уведомления отключены пользователем.')

        # 2. Ставим в очередь (реальный путь моста)
        site = getattr(settings, 'SITE_URL', '')
        notif = queue_user_notification(
            recipient=user,
            title='Проверка связи',
            message='Это тестовое уведомление UstozHub. Если вы его видите — Telegram подключён правильно. ✅',
            action_url=site,
            button_text='🌐 Открыть сайт',
            category='success',
        )
        if notif is None:
            raise CommandError(
                'create_notification вернул None — уведомление не поставлено '
                '(проверьте started_bot/notifications_enabled).'
            )
        self.stdout.write(self.style.SUCCESS(f'В очередь поставлено: {notif.id} (status={notif.status})'))

        # 3. Отправка
        if opts['flush']:
            sent = process_notification_queue(batch_size=10)
            notif.refresh_from_db()
            self.stdout.write(f'Обработано из очереди: {sent} | статус уведомления: {notif.status}')
            if notif.status == 'sent':
                self.stdout.write(self.style.SUCCESS('✅ Доставлено в Telegram — проверьте чат с ботом.'))
            else:
                self.stdout.write(self.style.ERROR(
                    f'❌ Не отправлено. last_error: {notif.last_error or "—"}'
                ))
        else:
            self.stdout.write(
                'Уведомление в очереди. Его заберёт демон telegram-bot.service '
                '(до ~10 сек). Для немедленной отправки добавьте --flush.'
            )
