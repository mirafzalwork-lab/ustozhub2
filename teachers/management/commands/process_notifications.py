"""
Management команда для обработки очереди уведомлений
Используется как fallback если Celery не настроен
"""

import time
import logging
from django.core.management.base import BaseCommand
from telegram_bot.notification_service import process_notification_queue

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Обрабатывает очередь Telegram уведомлений. Fallback для систем без Celery.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--daemon',
            action='store_true',
            help='Запустить в режиме демона (непрерывная обработка)',
        )
        parser.add_argument(
            '--interval',
            type=int,
            default=10,
            help='Интервал между обработками в секундах (по умолчанию: 10)',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=10,
            help='Размер батча для обработки (по умолчанию: 10)',
        )
        parser.add_argument(
            '--once',
            action='store_true',
            help='Обработать один батч и завершить',
        )

    def handle(self, *args, **options):
        daemon_mode = options['daemon']
        once_mode = options['once']
        interval = options['interval']
        batch_size = options['batch_size']
        
        if once_mode:
            # Обработать один раз
            self.stdout.write("🔄 Обработка одного батча уведомлений...")
            sent_count = process_notification_queue(batch_size=batch_size)
            self.stdout.write(
                self.style.SUCCESS(f"✅ Обработано уведомлений: {sent_count}")
            )
            return
        
        if daemon_mode:
            # Режим демона
            self.stdout.write(
                self.style.WARNING(
                    f"🔄 Запуск в режиме демона. Интервал: {interval}с, Размер батча: {batch_size}"
                )
            )
            self.stdout.write(
                self.style.WARNING("Нажмите Ctrl+C для остановки")
            )
            
            try:
                while True:
                    try:
                        sent_count = process_notification_queue(batch_size=batch_size)
                        
                        if sent_count > 0:
                            self.stdout.write(
                                f"✅ [{time.strftime('%Y-%m-%d %H:%M:%S')}] Обработано: {sent_count}"
                            )
                        
                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(
                                f"❌ [{time.strftime('%Y-%m-%d %H:%M:%S')}] Ошибка: {e}"
                            )
                        )
                        logger.error(f"Ошибка обработки очереди: {e}", exc_info=True)
                    
                    time.sleep(interval)
                    
            except KeyboardInterrupt:
                self.stdout.write(
                    self.style.WARNING("\n⚠️  Остановка демона...")
                )
                self.stdout.write(
                    self.style.SUCCESS("✅ Демон остановлен")
                )
        else:
            # По умолчанию - один раз
            self.stdout.write("🔄 Обработка очереди уведомлений...")
            sent_count = process_notification_queue(batch_size=batch_size)
            self.stdout.write(
                self.style.SUCCESS(f"✅ Обработано уведомлений: {sent_count}")
            )
