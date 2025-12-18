#!/usr/bin/env python
"""
Тестовый скрипт для проверки массовой рассылки
"""
import os
import sys
import django

# Добавляем путь к проекту
sys.path.append('/Users/humoyunswe/Desktop/ustozhubuz')

# Настраиваем Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from teachers.models import TelegramUser
from teachers.admin_telegram_service import AdminTelegramService

def test_broadcast():
    """Тестирует функцию массовой рассылки"""
    
    # Получаем всех пользователей
    users = TelegramUser.objects.all()
    
    if not users.exists():
        print("❌ Нет Telegram пользователей для тестирования")
        return
    
    print(f"📋 Найдено пользователей: {users.count()}")
    
    # Создаем сервис
    service = AdminTelegramService()
    
    if not service.bot:
        print("❌ Telegram bot не инициализирован. Проверьте TELEGRAM_BOT_TOKEN в настройках.")
        return
    
    # Тестовое сообщение
    test_message = "🔔 Тестовое сообщение массовой рассылки\n\nЭто проверка работы системы уведомлений."
    
    print(f"📤 Отправляем тестовое сообщение...")
    
    try:
        # Вызываем функцию массовой рассылки
        stats = service.send_to_selected_users(
            telegram_users=list(users),
            message=test_message,
            parse_mode='Markdown'
        )
        
        print("\n✅ Результаты отправки:")
        print(f"   Успешно: {stats['success']}")
        print(f"   Ошибок: {stats['failed']}")
        print(f"   Всего: {stats['total']}")
        
        if stats['details']:
            print("\n📋 Подробности:")
            for detail in stats['details']:
                status_icon = "✅" if detail['status'] == 'success' else "❌"
                print(f"   {status_icon} {detail['user']}: {detail.get('reason', 'Успешно отправлено')}")
        
    except Exception as e:
        print(f"❌ Ошибка при отправке: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    test_broadcast()