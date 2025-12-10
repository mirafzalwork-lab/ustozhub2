#!/usr/bin/env python3
"""
Скрипт для создания тестового сообщения платформы
"""
import os
import sys
import django

# Добавляем путь к проекту
sys.path.append('/Users/humoyunswe/Desktop/Projects/ustozhubuz')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

# Инициализируем Django
django.setup()

from teachers.models import PlatformMessage
from django.contrib.auth import get_user_model

User = get_user_model()

def create_test_message():
    """Создать тестовое сообщение платформы"""
    
    try:
        # Получаем первого пользователя или создаем админа
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            admin_user = User.objects.filter(is_staff=True).first()
        if not admin_user:
            admin_user = User.objects.first()
        
        if not admin_user:
            print("❌ Нет пользователей в системе!")
            return
        
        # Создаем тестовое сообщение
        message = PlatformMessage.objects.create(
            title="🎉 Добро пожаловать в UstozHub!",
            content="Это тестовое сообщение платформы. Здесь будут отображаться важные уведомления и новости.",
            message_type="info",
            priority=5,
            is_active=True,
            show_to_all=False,
            show_to_guests=True,
            show_to_teachers=True,
            show_to_students=True,
            created_by=admin_user
        )
        
        print(f"✅ Создано тестовое сообщение: {message.title}")
        print(f"📝 ID: {message.id}")
        print(f"👥 Показывать гостям: {message.show_to_guests}")
        print(f"👨‍🏫 Показывать учителям: {message.show_to_teachers}")
        print(f"👨‍🎓 Показывать ученикам: {message.show_to_students}")
        
        # Проверяем общее количество сообщений
        total_messages = PlatformMessage.objects.count()
        active_messages = PlatformMessage.objects.filter(is_active=True).count()
        
        print(f"\n📊 Статистика:")
        print(f"Всего сообщений: {total_messages}")
        print(f"Активных сообщений: {active_messages}")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    create_test_message()