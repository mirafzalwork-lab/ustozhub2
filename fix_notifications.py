#!/usr/bin/env python3
"""
Исправление контекст-процессора и проверка данных
"""
import os, sys, django

sys.path.append('/Users/humoyunswe/Desktop/Projects/ustozhubuz')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from teachers.models import PlatformMessage
from django.contrib.auth import get_user_model
from django.db import connection

User = get_user_model()

print("🔍 ДИАГНОСТИКА ПРОБЛЕМЫ\n")

# 1. Проверяем структуру таблицы
print("1️⃣ Структура таблицы teachers_platformmessage:")
with connection.cursor() as cursor:
    cursor.execute("PRAGMA table_info(teachers_platformmessage)")
    columns = cursor.fetchall()
    for col in columns:
        print(f"   {col[1]} ({col[2]})")

# 2. Проверяем есть ли поле show_to_guests
has_show_to_guests = any('show_to_guests' in str(col) for col in columns)
print(f"\n2️⃣ Поле show_to_guests существует: {'✅ Да' if has_show_to_guests else '❌ Нет'}")

if not has_show_to_guests:
    print("\n🛠️ ИСПРАВЛЕНИЕ: Добавляем поле show_to_guests...")
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE teachers_platformmessage ADD COLUMN show_to_guests BOOLEAN DEFAULT 1")
        print("✅ Поле добавлено")

# 3. Обновляем существующие записи
print("\n3️⃣ Обновляем существующие записи...")
PlatformMessage.objects.filter(show_to_guests__isnull=True).update(show_to_guests=True)
print("✅ Записи обновлены")

# 4. Тестируем контекст-процессор снова
print("\n4️⃣ Тестируем контекст-процессор...")
from teachers.context_processors import platform_messages
from django.contrib.auth.models import AnonymousUser

class MockRequest:
    def __init__(self):
        self.user = AnonymousUser()

mock_request = MockRequest()
context_data = platform_messages(mock_request)

print(f"   platform_messages: {len(context_data.get('platform_messages', []))} сообщений")
print(f"   unread_count: {context_data.get('unread_platform_messages_count', 0)}")

# 5. Проверяем каждое сообщение отдельно
messages = PlatformMessage.objects.filter(is_active=True)
print(f"\n5️⃣ Проверяем каждое сообщение:")
for msg in messages:
    shows_to_guest = msg.should_show_to_user(None)
    print(f"   '{msg.title}' -> should_show_to_user(None): {shows_to_guest}")
    print(f"     show_to_guests: {msg.show_to_guests}")
    print(f"     is_active: {msg.is_active}")

print(f"\n🎯 РЕЗУЛЬТАТ: {'✅ Готово!' if len(context_data.get('platform_messages', [])) > 0 else '❌ Все еще проблема'}")