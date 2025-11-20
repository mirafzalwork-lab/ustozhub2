#!/usr/bin/env python3
"""
Скрипт для тестирования отправки Telegram сообщений
"""

import os
import sys
import django

# Добавляем текущую директорию в путь Python
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Настраиваем Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.conf import settings
from teachers.models import TelegramUser
from teachers.admin_telegram_service import admin_telegram_service
import logging

# Настраиваем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_telegram_settings():
    """Проверяет настройки Telegram бота"""
    print("🔧 Проверка настроек Telegram бота...")
    
    bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', None)
    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN не установлен в настройках!")
        return False
    
    print(f"✅ TELEGRAM_BOT_TOKEN найден (длина: {len(bot_token)} символов)")
    
    # Проверяем формат токена
    if ':' not in bot_token:
        print("❌ Неверный формат токена. Должен быть: XXXXXXXXX:XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX")
        return False
    
    parts = bot_token.split(':')
    if len(parts) != 2:
        print("❌ Неверный формат токена. Должен содержать одно двоеточие")
        return False
        
    bot_id, bot_secret = parts
    if not bot_id.isdigit() or len(bot_secret) != 35:
        print("❌ Неверный формат токена. Проверьте токен от @BotFather")
        return False
    
    print("✅ Формат токена корректный")
    return True

def test_telegram_users():
    """Проверяет пользователей Telegram"""
    print("\n👥 Проверка пользователей Telegram...")
    
    total_users = TelegramUser.objects.count()
    active_users = TelegramUser.objects.filter(started_bot=True).count()
    notif_enabled = TelegramUser.objects.filter(notifications_enabled=True).count()
    ready_users = TelegramUser.objects.filter(started_bot=True, notifications_enabled=True).count()
    
    print(f"📊 Статистика пользователей:")
    print(f"   Всего: {total_users}")
    print(f"   Активировали бота: {active_users}")
    print(f"   Уведомления включены: {notif_enabled}")
    print(f"   Готовы к получению: {ready_users}")
    
    if ready_users == 0:
        print("⚠️  Нет пользователей готовых к получению сообщений")
        print("   Убедитесь что пользователи нажали /start в боте")
        return False
    
    return True

def test_send_message():
    """Тестирует отправку сообщения"""
    print("\n📤 Тест отправки сообщения...")
    
    # Находим первого готового пользователя
    test_user = TelegramUser.objects.filter(
        started_bot=True, 
        notifications_enabled=True
    ).first()
    
    if not test_user:
        print("❌ Нет пользователей для тестирования")
        return False
    
    print(f"👤 Тестируем с пользователем: {test_user.first_name} (ID: {test_user.telegram_id})")
    
    test_message = "🧪 Тестовое сообщение от админ-панели UstozHub\n\nЭто тест системы отправки сообщений."
    
    try:
        success = admin_telegram_service.send_message_sync(
            telegram_id=test_user.telegram_id,
            text=test_message,
            parse_mode='Markdown'
        )
        
        if success:
            print("✅ Сообщение отправлено успешно!")
            return True
        else:
            print("❌ Не удалось отправить сообщение")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка при отправке: {e}")
        return False

def main():
    """Основная функция диагностики"""
    print("🚀 Диагностика системы Telegram сообщений UstozHub")
    print("=" * 60)
    
    # Проверка настроек
    if not test_telegram_settings():
        print("\n❌ Проблемы с настройками. Проверьте TELEGRAM_BOT_TOKEN в core/settings.py")
        return
    
    # Проверка пользователей
    if not test_telegram_users():
        print("\n⚠️  Нет пользователей для отправки сообщений")
        print("   1. Убедитесь что пользователи зарегистрированы в Telegram боте")
        print("   2. Попросите их нажать /start")
        print("   3. Проверьте что уведомления включены")
        return
    
    # Тест отправки
    if test_send_message():
        print("\n🎉 Все тесты прошли успешно!")
        print("   Система отправки Telegram сообщений работает корректно")
    else:
        print("\n❌ Тест отправки не прошел")
        print("   Проверьте логи сервера для получения подробной информации")
    
    print("\n📋 Рекомендации:")
    print("   • Проверьте логи Django сервера на наличие ошибок")
    print("   • Убедитесь что бот не заблокирован пользователями")
    print("   • Проверьте что токен бота действителен")

if __name__ == "__main__":
    main()