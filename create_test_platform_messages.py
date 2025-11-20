#!/usr/bin/env python3
"""
Скрипт для создания тестового сообщения платформы
"""

import os
import sys
import django

# Добавляем текущую директорию в путь Python
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Настраиваем Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model  
from teachers.models import PlatformMessage
from django.utils import timezone

User = get_user_model()

def create_test_platform_message():
    """Создает тестовое сообщение платформы"""
    
    try:
        # Получаем первого суперпользователя для создания сообщения
        admin_user = User.objects.filter(is_superuser=True).first()
        
        if not admin_user:
            print("❌ Не найден суперпользователь. Создайте admin пользователя сначала.")
            return False
        
        # Создаем тестовое сообщение
        message = PlatformMessage.objects.create(
            title="🎉 Добро пожаловать на UstozHub!",
            content="""
Добро пожаловать на нашу платформу для поиска преподавателей!

Здесь вы можете:
• Найти квалифицированных учителей
• Связаться с ними напрямую
• Оставлять отзывы и оценки
• Получать уведомления о новых сообщениях

Спасибо, что выбрали UstozHub! 🚀
            """.strip(),
            message_type='announcement',
            is_active=True,
            show_to_all=True,
            show_to_teachers=True,
            show_to_students=True,
            created_by=admin_user,
            priority=10
        )
        
        print(f"✅ Создано тестовое сообщение: {message.title}")
        print(f"   ID: {message.id}")
        print(f"   Тип: {message.get_message_type_display()}")
        print(f"   Автор: {message.created_by.username}")
        
        # Создаем еще одно сообщение для теста
        message2 = PlatformMessage.objects.create(
            title="🔧 Техническое обслуживание",
            content="Завтра с 02:00 до 04:00 по московскому времени планируется техническое обслуживание платформы. Возможны кратковременные перебои в работе.",
            message_type='warning',
            is_active=True,
            show_to_all=True,
            show_to_teachers=True,
            show_to_students=True,
            created_by=admin_user,
            priority=5,
            expires_at=timezone.now() + timezone.timedelta(days=2)
        )
        
        print(f"✅ Создано второе тестовое сообщение: {message2.title}")
        print(f"   ID: {message2.id}")
        print(f"   Истекает: {message2.expires_at}")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при создании тестового сообщения: {e}")
        return False

def show_existing_messages():
    """Показывает существующие сообщения платформы"""
    messages = PlatformMessage.objects.all().order_by('-created_at')
    
    if not messages:
        print("📭 Сообщения платформы не найдены")
        return
    
    print(f"\n📋 Найдено сообщений: {messages.count()}")
    print("-" * 60)
    
    for msg in messages:
        status = "🟢 Активно" if msg.is_active else "🔴 Неактивно"
        expires = f" (истекает {msg.expires_at})" if msg.expires_at else ""
        
        print(f"ID: {msg.id}")
        print(f"Заголовок: {msg.title}")
        print(f"Тип: {msg.get_message_type_display()}")
        print(f"Статус: {status}{expires}")
        print(f"Автор: {msg.created_by.username}")
        print(f"Создано: {msg.created_at}")
        print("-" * 60)

if __name__ == "__main__":
    print("🚀 Создание тестовых сообщений платформы...")
    print()
    
    # Показываем существующие сообщения
    show_existing_messages()
    
    # Создаем тестовые сообщения если их нет
    if PlatformMessage.objects.count() == 0:
        create_test_platform_message()
        print("\n📋 Обновленный список сообщений:")
        show_existing_messages()
    else:
        print("\n✅ Тестовые сообщения уже существуют")
    
    print("\n🎯 Теперь зайдите на сайт и проверьте навбар - должна появиться иконка уведомлений платформы!")
    print("🔧 Для управления сообщениями перейдите: /admin-dashboard/platform-messages/")