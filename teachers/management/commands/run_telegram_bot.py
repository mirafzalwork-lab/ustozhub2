"""Management-команда запуска интерактивного Telegram-бота (polling).

Обёртка над ``telegram_bot.bot.main()``, чтобы бот запускался штатным
Django-механизмом (``manage.py run_telegram_bot``), а не отдельным
``python telegram_bot/bot.py`` в обход manage.py. Так гарантированно
подхватывается окружение/настройки, а systemd-юнит выглядит единообразно
с остальными командами (process_notifications и т.п.).
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Запускает интерактивный Telegram-бот в режиме polling (команды, WebApp, привязка аккаунта).'

    def handle(self, *args, **options):
        # Импортируем внутри handle: модуль bot при импорте дергает django.setup()
        # и телеграм-клиент — не нужно на этапе сбора команд (manage.py help).
        from telegram_bot.bot import main
        main()
