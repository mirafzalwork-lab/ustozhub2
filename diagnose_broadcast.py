#!/usr/bin/env python
"""
Отладочный скрипт для диагностики массовой рассылки на продакшене
"""
import os
import sys
import django
import logging

sys.path.append('/Users/humoyunswe/Desktop/ustozhubuz')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from teachers.models import TelegramUser
from django.conf import settings

# Настраиваем логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def diagnose_broadcast_system():
    """Диагностика системы массовой рассылки"""
    
    print("🔍 ДИАГНОСТИКА СИСТЕМЫ МАССОВОЙ РАССЫЛКИ")
    print("=" * 50)
    
    # 1. Проверка настроек
    print("\n1️⃣ ПРОВЕРКА НАСТРОЕК:")
    
    telegram_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', None)
    if telegram_token:
        print(f"   ✅ TELEGRAM_BOT_TOKEN: установлен (...{telegram_token[-10:]})")
    else:
        print(f"   ❌ TELEGRAM_BOT_TOKEN: НЕ УСТАНОВЛЕН!")
        return False
    
    # 2. Проверка базы данных
    print("\n2️⃣ ПРОВЕРКА БАЗЫ ДАННЫХ:")
    
    try:
        total_users = TelegramUser.objects.count()
        active_users = TelegramUser.objects.filter(
            started_bot=True,
            notifications_enabled=True
        ).count()
        
        print(f"   📊 Всего пользователей: {total_users:,}")
        print(f"   ✅ Активных: {active_users:,}")
        print(f"   ❌ Неактивных: {total_users - active_users:,}")
        
        if total_users == 0:
            print("   ⚠️  ВНИМАНИЕ: В базе нет пользователей!")
            return False
            
    except Exception as e:
        print(f"   ❌ Ошибка доступа к базе: {e}")
        return False
    
    # 3. Проверка инициализации сервиса
    print("\n3️⃣ ПРОВЕРКА TELEGRAM СЕРВИСА:")
    
    try:
        from teachers.admin_telegram_service import AdminTelegramService
        service = AdminTelegramService()
        
        if service.bot:
            print(f"   ✅ Telegram bot создан успешно")
        else:
            print(f"   ❌ Telegram bot НЕ СОЗДАН!")
            return False
            
    except Exception as e:
        print(f"   ❌ Ошибка создания сервиса: {e}")
        return False
    
    # 4. Проверка импорта в views
    print("\n4️⃣ ПРОВЕРКА ИМПОРТА В VIEWS:")
    
    try:
        from teachers.admin_telegram_service import admin_telegram_service
        print(f"   ✅ admin_telegram_service импортирован")
        
        if admin_telegram_service.bot:
            print(f"   ✅ Глобальный экземпляр сервиса работает")
        else:
            print(f"   ❌ Глобальный экземпляр сервиса НЕ РАБОТАЕТ!")
            return False
            
    except Exception as e:
        print(f"   ❌ Ошибка импорта admin_telegram_service: {e}")
        return False
    
    # 5. Тест простой отправки
    print("\n5️⃣ ТЕСТ ПРОСТОЙ ОТПРАВКИ:")
    
    if active_users > 0:
        test_user = TelegramUser.objects.filter(
            started_bot=True,
            notifications_enabled=True
        ).first()
        
        print(f"   🎯 Тестовый пользователь: {test_user.first_name} (ID: {test_user.telegram_id})")
        
        try:
            result = admin_telegram_service.send_message_sync(
                telegram_id=test_user.telegram_id,
                text="🧪 Диагностический тест системы рассылки",
                parse_mode='Markdown'
            )
            
            if result['success']:
                print(f"   ✅ Тестовое сообщение отправлено успешно!")
            else:
                print(f"   ❌ Ошибка отправки: {result['error_message']}")
                
        except Exception as e:
            print(f"   ❌ Критическая ошибка тестовой отправки: {e}")
            return False
    
    # 6. Анализ возможных проблем
    print("\n6️⃣ АНАЛИЗ ПРОБЛЕМНЫХ ПОЛЬЗОВАТЕЛЕЙ:")
    
    problem_categories = {
        'invalid_ids': TelegramUser.objects.filter(telegram_id__lt=1000000).count(),
        'very_old': TelegramUser.objects.filter(telegram_id__regex=r'^123456.*').count(),
        'not_started': TelegramUser.objects.filter(started_bot=False).count(),
        'notifications_off': TelegramUser.objects.filter(notifications_enabled=False).count(),
    }
    
    for category, count in problem_categories.items():
        if count > 0:
            print(f"   ⚠️  {category}: {count:,} пользователей")
    
    print(f"\n✅ ДИАГНОСТИКА ЗАВЕРШЕНА!")
    print(f"📊 Готово к рассылке: {active_users:,} из {total_users:,} пользователей")
    
    return True

if __name__ == '__main__':
    try:
        diagnose_broadcast_system()
    except Exception as e:
        print(f"❌ Критическая ошибка диагностики: {e}")
        import traceback
        traceback.print_exc()