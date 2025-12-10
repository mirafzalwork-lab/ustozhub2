#!/usr/bin/env python3
"""
Быстрая проверка данных платформы
"""
import os, sys, django

sys.path.append('/Users/humoyunswe/Desktop/Projects/ustozhubuz')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from teachers.models import PlatformMessage
from django.contrib.auth import get_user_model

User = get_user_model()

print("🔍 ПРОВЕРКА ДАННЫХ ПЛАТФОРМЫ\n")

# Проверяем пользователей
user_count = User.objects.count()
print(f"👥 Пользователей в системе: {user_count}")

# Проверяем сообщения
messages = PlatformMessage.objects.all()
active_messages = PlatformMessage.objects.filter(is_active=True)

print(f"📄 Всего сообщений: {messages.count()}")
print(f"✅ Активных сообщений: {active_messages.count()}")

if active_messages.count() > 0:
    print("\n📋 АКТИВНЫЕ СООБЩЕНИЯ:")
    for i, msg in enumerate(active_messages[:5], 1):
        print(f"  {i}. {msg.title}")
        print(f"     Тип: {msg.message_type}")
        print(f"     Показывать гостям: {'✅' if msg.show_to_guests else '❌'}")
        print(f"     Показывать учителям: {'✅' if msg.show_to_teachers else '❌'}")
        print(f"     Показывать ученикам: {'✅' if msg.show_to_students else '❌'}")
        print()

# Проверяем контекст-процессор
from teachers.context_processors import platform_messages
from django.http import HttpRequest
from django.contrib.auth.models import AnonymousUser

# Тестируем для гостя
class MockRequest:
    def __init__(self):
        self.user = AnonymousUser()

mock_request = MockRequest()
context_data = platform_messages(mock_request)

print("🧪 ТЕСТ КОНТЕКСТ-ПРОЦЕССОРА (для гостя):")
print(f"   platform_messages: {len(context_data.get('platform_messages', []))} сообщений")
print(f"   unread_count: {context_data.get('unread_platform_messages_count', 0)}")

if len(context_data.get('platform_messages', [])) == 0:
    print("\n❌ ПРОБЛЕМА: Контекст-процессор не возвращает сообщения для гостей")
    print("   Возможные причины:")
    print("   1. Нет сообщений с show_to_guests=True")
    print("   2. Все сообщения неактивны (is_active=False)")
    print("   3. Ошибка в логике контекст-процессора")
else:
    print("\n✅ УСПЕХ: Контекст-процессор работает правильно!")

print(f"\n🎯 ИТОГ: {'Система готова!' if active_messages.count() > 0 and len(context_data.get('platform_messages', [])) > 0 else 'Нужно исправить проблемы выше'}")