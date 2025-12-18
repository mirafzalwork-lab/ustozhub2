#!/usr/bin/env python
"""
Скрипт для добавления нового Telegram пользователя
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

def add_telegram_user():
    """Добавляет нового Telegram пользователя @khumoyun_04"""
    
    # Проверяем, существует ли уже пользователь с таким username
    existing_user = TelegramUser.objects.filter(telegram_username='khumoyun_04').first()
    
    if existing_user:
        print(f"Пользователь @khumoyun_04 уже существует: {existing_user}")
        return existing_user
    
    # Создаем нового пользователя
    telegram_user = TelegramUser.objects.create(
        telegram_id=1234567890,  # Временный ID, будет обновлен при первом контакте с ботом
        telegram_username='khumoyun_04',
        first_name='Khumoyun',
        last_name='',
        language_code='ru',
        notifications_enabled=True,
        started_bot=False
    )
    
    print(f"✅ Успешно создан Telegram пользователь:")
    print(f"   ID в базе: {telegram_user.id}")
    print(f"   Telegram ID: {telegram_user.telegram_id}")
    print(f"   Username: @{telegram_user.telegram_username}")
    print(f"   Имя: {telegram_user.first_name}")
    print(f"   Уведомления: {'включены' if telegram_user.notifications_enabled else 'выключены'}")
    print(f"   Дата создания: {telegram_user.created_at}")
    
    return telegram_user

if __name__ == '__main__':
    try:
        user = add_telegram_user()
        print("\n✅ Операция завершена успешно!")
    except Exception as e:
        print(f"❌ Ошибка при создании пользователя: {e}")
        sys.exit(1)