#!/usr/bin/env python
"""
Тестовый скрипт для проверки системы уведомлений
Запуск: pipenv run python test_notifications.py
"""

import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from teachers.models import User, NotificationQueue, NotificationLog, TelegramUser
from telegram_bot.notification_service import notification_service, process_notification_queue
from django.db.models import Count


def print_header(text):
    """Печатает красивый заголовок"""
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60 + "\n")


def print_stats():
    """Показывает статистику очереди"""
    print_header("📊 СТАТИСТИКА ОЧЕРЕДИ")
    
    stats = NotificationQueue.objects.values('status').annotate(count=Count('id'))
    
    if not stats:
        print("✨ Очередь пуста")
        return
    
    for stat in stats:
        status_emoji = {
            'pending': '⏳',
            'processing': '🔄',
            'sent': '✅',
            'failed': '❌',
            'cancelled': '🚫'
        }
        emoji = status_emoji.get(stat['status'], '❓')
        print(f"{emoji} {stat['status']}: {stat['count']}")


def print_recent_logs():
    """Показывает последние логи"""
    print_header("📝 ПОСЛЕДНИЕ ЛОГИ (10)")
    
    logs = NotificationLog.objects.select_related('notification').order_by('-timestamp')[:10]
    
    if not logs:
        print("📭 Логов нет")
        return
    
    for log in logs:
        status_emoji = '✅' if log.status == 'success' else '❌'
        print(f"{status_emoji} [{log.timestamp.strftime('%H:%M:%S')}] "
              f"Попытка {log.attempt_number} - {log.status}")
        if log.error_message:
            print(f"   ⚠️  {log.error_message[:80]}")


def print_failed_notifications():
    """Показывает проваленные уведомления"""
    print_header("❌ ПРОВАЛЕННЫЕ УВЕДОМЛЕНИЯ")
    
    failed = NotificationQueue.objects.filter(status='failed').order_by('-updated_at')[:5]
    
    if not failed:
        print("✨ Нет проваленных уведомлений")
        return
    
    for n in failed:
        print(f"ID: {str(n.id)[:8]}...")
        print(f"   Получатель: {n.recipient.username}")
        print(f"   Попытки: {n.retry_count}/{n.max_retries}")
        print(f"   Ошибка: {n.last_error[:80] if n.last_error else 'N/A'}")
        print(f"   Можно повторить: {'✅ Да' if n.can_retry() else '❌ Нет'}")
        print()


def check_telegram_users():
    """Проверяет Telegram пользователей"""
    print_header("👥 TELEGRAM ПОЛЬЗОВАТЕЛИ")
    
    total = TelegramUser.objects.count()
    enabled = TelegramUser.objects.filter(notifications_enabled=True).count()
    started = TelegramUser.objects.filter(started_bot=True).count()
    
    print(f"Всего: {total}")
    print(f"С включенными уведомлениями: {enabled}")
    print(f"Запустили бота: {started}")
    
    if total == 0:
        print("\n⚠️  Нет пользователей с подключенным Telegram!")
        print("Попросите пользователей запустить бота: /start")


def create_test_notification():
    """Создает тестовое уведомление"""
    print_header("🧪 СОЗДАНИЕ ТЕСТОВОГО УВЕДОМЛЕНИЯ")
    
    # Найти пользователя с Telegram
    telegram_user = TelegramUser.objects.filter(
        notifications_enabled=True,
        started_bot=True
    ).select_related('user').first()
    
    if not telegram_user:
        print("❌ Нет пользователей с включенными уведомлениями!")
        print("Попросите пользователя:")
        print("1. Запустить бота: /start")
        print("2. Включить уведомления: /notifications")
        return None
    
    user = telegram_user.user
    print(f"✅ Найден пользователь: {user.username} ({user.get_full_name()})")
    
    # Создать уведомление
    notification = notification_service.create_notification(
        recipient=user,
        notification_type='new_message',
        title='🧪 Тестовое уведомление',
        message='Это тестовое сообщение для проверки системы уведомлений!',
        data={
            'test': True,
            'url': 'https://teacherhub.com',
            'button_text': '🚀 Открыть'
        }
    )
    
    if notification:
        print(f"✅ Уведомление создано!")
        print(f"   ID: {notification.id}")
        print(f"   Статус: {notification.status}")
        print(f"   Запланировано: {notification.scheduled_at}")
        return notification
    else:
        print("❌ Не удалось создать уведомление")
        return None


def process_queue():
    """Обрабатывает очередь"""
    print_header("🚀 ОБРАБОТКА ОЧЕРЕДИ")
    
    pending = NotificationQueue.objects.filter(status='pending').count()
    print(f"⏳ Pending уведомлений: {pending}")
    
    if pending == 0:
        print("✨ Нечего обрабатывать")
        return
    
    print("🔄 Обрабатываем...")
    sent_count = process_notification_queue(batch_size=10)
    
    print(f"✅ Обработано: {sent_count}/{pending}")


def main():
    """Главная функция"""
    print("\n")
    print("╔════════════════════════════════════════════════════════════╗")
    print("║   🔔 ТЕСТ СИСТЕМЫ TELEGRAM УВЕДОМЛЕНИЙ                    ║")
    print("╚════════════════════════════════════════════════════════════╝")
    
    while True:
        print("\n" + "-" * 60)
        print("МЕНЮ:")
        print("  1. 📊 Показать статистику")
        print("  2. 📝 Показать последние логи")
        print("  3. ❌ Показать проваленные уведомления")
        print("  4. 👥 Проверить Telegram пользователей")
        print("  5. 🧪 Создать тестовое уведомление")
        print("  6. 🚀 Обработать очередь")
        print("  7. 🔄 Полный тест (создать + обработать)")
        print("  0. ❌ Выход")
        print("-" * 60)
        
        choice = input("\nВыберите действие: ").strip()
        
        if choice == '1':
            print_stats()
        elif choice == '2':
            print_recent_logs()
        elif choice == '3':
            print_failed_notifications()
        elif choice == '4':
            check_telegram_users()
        elif choice == '5':
            create_test_notification()
        elif choice == '6':
            process_queue()
        elif choice == '7':
            notification = create_test_notification()
            if notification:
                import time
                print("\n⏳ Ждем 2 секунды...")
                time.sleep(2)
                process_queue()
                print("\n✨ Проверьте Telegram!")
        elif choice == '0':
            print("\n👋 До свидания!")
            break
        else:
            print("❌ Неверный выбор!")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Прервано пользователем")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
