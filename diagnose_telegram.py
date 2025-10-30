#!/usr/bin/env python3
"""
Скрипт для диагностики проблем с отправкой Telegram сообщений
"""

import os
import sys
import django

# Настройка Django
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from teachers.models import TelegramUser, User
from telegram_bot.notifications import notification_service


def check_telegram_users():
    """Проверить состояние Telegram пользователей"""
    print("🔍 ДИАГНОСТИКА TELEGRAM ПОЛЬЗОВАТЕЛЕЙ")
    print("=" * 60)
    
    total_users = TelegramUser.objects.count()
    print(f"📊 Всего Telegram пользователей: {total_users}")
    
    if total_users == 0:
        print("❌ Нет пользователей в базе данных!")
        return
    
    # Статистика по статусам
    notifications_on = TelegramUser.objects.filter(notifications_enabled=True).count()
    started_bot = TelegramUser.objects.filter(started_bot=True).count()
    ready_to_receive = TelegramUser.objects.filter(
        notifications_enabled=True, 
        started_bot=True
    ).count()
    
    print(f"🔔 Уведомления включены: {notifications_on}")
    print(f"🤖 Запустили бота (/start): {started_bot}")
    print(f"✅ Готовы получать сообщения: {ready_to_receive}")
    
    # Проблемные пользователи
    print("\n⚠️ ПРОБЛЕМНЫЕ ПОЛЬЗОВАТЕЛИ:")
    print("-" * 40)
    
    # Не запустили бота
    not_started = TelegramUser.objects.filter(
        notifications_enabled=True,
        started_bot=False
    )
    
    if not_started.exists():
        print(f"\n🚫 Не запустили бота ({not_started.count()} чел.):")
        for user in not_started[:5]:  # Показываем первых 5
            print(f"  • {user.first_name} {user.last_name} (@{user.telegram_username or 'нет'}) - ID: {user.telegram_id}")
        if not_started.count() > 5:
            print(f"  ... и еще {not_started.count() - 5} пользователей")
    
    # Отключили уведомления
    notifications_off = TelegramUser.objects.filter(
        notifications_enabled=False,
        started_bot=True
    )
    
    if notifications_off.exists():
        print(f"\n🔕 Отключили уведомления ({notifications_off.count()} чел.):")
        for user in notifications_off[:5]:
            print(f"  • {user.first_name} {user.last_name} (@{user.telegram_username or 'нет'}) - ID: {user.telegram_id}")
        if notifications_off.count() > 5:
            print(f"  ... и еще {notifications_off.count() - 5} пользователей")
    
    # Не привязаны к аккаунту
    not_linked = TelegramUser.objects.filter(user__isnull=True)
    if not_linked.exists():
        print(f"\n🔗 Не привязаны к аккаунту ({not_linked.count()} чел.):")
        for user in not_linked[:5]:
            print(f"  • {user.first_name} {user.last_name} (@{user.telegram_username or 'нет'}) - ID: {user.telegram_id}")
        if not_linked.count() > 5:
            print(f"  ... и еще {not_linked.count() - 5} пользователей")


def test_message_sending():
    """Тестировать отправку сообщения"""
    print("\n🧪 ТЕСТ ОТПРАВКИ СООБЩЕНИЯ")
    print("=" * 60)
    
    # Находим первого готового пользователя
    ready_user = TelegramUser.objects.filter(
        notifications_enabled=True,
        started_bot=True
    ).first()
    
    if not ready_user:
        print("❌ Нет пользователей, готовых к получению сообщений!")
        return
    
    print(f"📤 Тестируем отправку пользователю: {ready_user.first_name} (@{ready_user.telegram_username})")
    
    test_message = "🧪 Тестовое сообщение от системы диагностики"
    
    try:
        success = notification_service.send_message_sync(
            telegram_id=ready_user.telegram_id,
            text=test_message
        )
        
        if success:
            print("✅ Тестовое сообщение отправлено успешно!")
        else:
            print("❌ Ошибка отправки тестового сообщения")
            print("💡 Возможные причины:")
            print("  • Бот не запущен")
            print("  • Неверный токен бота")
            print("  • Пользователь заблокировал бота")
            
    except Exception as e:
        print(f"❌ Исключение при отправке: {e}")


def check_bot_config():
    """Проверить конфигурацию бота"""
    print("\n⚙️ КОНФИГУРАЦИЯ БОТА")
    print("=" * 60)
    
    from django.conf import settings
    
    bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', None)
    if bot_token:
        print(f"✅ Токен бота: {bot_token[:10]}...{bot_token[-10:]}")
    else:
        print("❌ Токен бота не установлен!")
        return
    
    site_url = getattr(settings, 'SITE_URL', None)
    if site_url:
        print(f"✅ URL сайта: {site_url}")
    else:
        print("❌ URL сайта не установлен!")
    
    webapp_url = getattr(settings, 'TELEGRAM_WEBAPP_URL', None)
    if webapp_url:
        print(f"✅ WebApp URL: {webapp_url}")
    else:
        print("❌ WebApp URL не установлен!")


def main():
    """Главная функция"""
    print("🔧 ДИАГНОСТИКА TELEGRAM ИНТЕГРАЦИИ")
    print("=" * 60)
    
    check_bot_config()
    check_telegram_users()
    test_message_sending()
    
    print("\n💡 РЕКОМЕНДАЦИИ:")
    print("-" * 40)
    print("1. Убедитесь, что бот запущен: ./start_bot.sh")
    print("2. Проверьте, что пользователи нажали /start в боте")
    print("3. Убедитесь, что у пользователей включены уведомления")
    print("4. Проверьте логи бота на наличие ошибок")
    print("5. Убедитесь, что токен бота корректный")


if __name__ == '__main__':
    main()

