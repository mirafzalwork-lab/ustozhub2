#!/usr/bin/env python
"""
Скрипт для просмотра всех Telegram пользователей
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

def list_telegram_users():
    """Показывает список всех Telegram пользователей"""
    
    users = TelegramUser.objects.all().order_by('-created_at')
    
    if not users:
        print("❌ Telegram пользователи не найдены")
        return
    
    print(f"📋 Найдено Telegram пользователей: {users.count()}")
    print("=" * 80)
    
    for i, user in enumerate(users, 1):
        print(f"{i}. ID в базе: {user.id}")
        print(f"   Telegram ID: {user.telegram_id}")
        print(f"   Username: @{user.telegram_username or 'не указан'}")
        print(f"   Имя: {user.first_name or 'не указано'}")
        print(f"   Фамилия: {user.last_name or 'не указана'}")
        print(f"   Язык: {user.language_code or 'не указан'}")
        print(f"   Уведомления: {'✅ включены' if user.notifications_enabled else '❌ выключены'}")
        print(f"   Запустил бота: {'✅ да' if user.started_bot else '❌ нет'}")
        print(f"   Связанный пользователь: {user.user.get_full_name() if user.user else '❌ не привязан'}")
        print(f"   Создан: {user.created_at.strftime('%d.%m.%Y %H:%M')}")
        print(f"   Последняя активность: {user.last_interaction.strftime('%d.%m.%Y %H:%M')}")
        print("-" * 40)

if __name__ == '__main__':
    try:
        list_telegram_users()
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        sys.exit(1)