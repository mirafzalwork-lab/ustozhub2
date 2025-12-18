#!/usr/bin/env python
"""
Скрипт для обновления статуса пользователя @khumoyun_04
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

def update_khumoyun_status():
    """Обновляет статус пользователя @khumoyun_04"""
    
    # Найдем пользователя
    user = TelegramUser.objects.filter(telegram_username='khumoyun_04').first()
    
    if not user:
        print("❌ Пользователь @khumoyun_04 не найден")
        return
    
    print(f"📋 Текущий статус пользователя @khumoyun_04:")
    print(f"   ID в базе: {user.id}")
    print(f"   Имя: {user.first_name}")
    print(f"   Запустил бота: {'✅ да' if user.started_bot else '❌ нет'}")
    print(f"   Уведомления: {'✅ включены' if user.notifications_enabled else '❌ выключены'}")
    
    # Обновляем статус
    user.started_bot = True
    user.notifications_enabled = True
    user.save()
    
    print(f"\n✅ Статус успешно обновлен!")
    print(f"   Запустил бота: ✅ да")
    print(f"   Уведомления: ✅ включены")
    print(f"   Теперь @khumoyun_04 будет получать массовые рассылки")

if __name__ == '__main__':
    try:
        update_khumoyun_status()
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        sys.exit(1)