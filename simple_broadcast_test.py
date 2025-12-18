#!/usr/bin/env python
"""
Простой тест массовой рассылки для диагностики
"""
import os
import sys
import django

sys.path.append('/Users/humoyunswe/Desktop/ustozhubuz')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from teachers.models import TelegramUser
from teachers.admin_telegram_service import AdminTelegramService

def simple_broadcast_test():
    """Простой тест массовой рассылки"""
    
    print("🧪 ПРОСТОЙ ТЕСТ МАССОВОЙ РАССЫЛКИ")
    print("=" * 40)
    
    # Получаем активных пользователей
    active_users = TelegramUser.objects.filter(
        started_bot=True,
        notifications_enabled=True
    )
    
    print(f"📋 Найдено активных пользователей: {active_users.count()}")
    
    if not active_users.exists():
        print("❌ Нет активных пользователей для тестирования")
        return
    
    # Создаем сервис
    service = AdminTelegramService()
    
    if not service.bot:
        print("❌ Бот не инициализирован")
        return
    
    print("🤖 Бот инициализирован успешно")
    
    # Тестируем отправку одному пользователю
    test_user = active_users.first()
    print(f"\n📤 Тестируем отправку одному пользователю: {test_user.first_name}")
    
    result = service.send_message_sync(
        telegram_id=test_user.telegram_id,
        text="🧪 Тестовое сообщение от системы UstozHub",
        parse_mode='Markdown'
    )
    
    print(f"Результат одиночной отправки: {result}")
    
    # Тестируем массовую рассылку
    print(f"\n📢 Тестируем массовую рассылку {active_users.count()} пользователям...")
    
    users_list = list(active_users)
    print(f"Конвертировали в список: {len(users_list)} пользователей")
    
    try:
        stats = service.send_to_selected_users(
            telegram_users=users_list,
            message="📢 Тестовое массовое сообщение от UstozHub",
            parse_mode='Markdown'
        )
        
        print(f"\n✅ Массовая рассылка завершена:")
        print(f"   Успешно: {stats['success']}")
        print(f"   Ошибок: {stats['failed']}")
        print(f"   Всего: {stats['total']}")
        
        if stats['details']:
            print(f"\n📋 Первые 5 результатов:")
            for i, detail in enumerate(stats['details'][:5]):
                status = "✅" if detail['status'] == 'success' else "❌"
                print(f"   {i+1}. {status} {detail['user']}: {detail['reason']}")
        
    except Exception as e:
        print(f"❌ Ошибка массовой рассылки: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    simple_broadcast_test()