#!/usr/bin/env python
"""
Быстрая отправка сообщений в Telegram
Использование: python send_telegram_message.py
"""

import os
import sys
import django

# Настройка Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from telegram_bot.notifications import notification_service
from teachers.models import TelegramUser


def show_statistics():
    """Показать статистику пользователей"""
    total = TelegramUser.objects.count()
    started = TelegramUser.objects.filter(started_bot=True).count()
    notifications_on = TelegramUser.objects.filter(notifications_enabled=True, started_bot=True).count()
    linked = TelegramUser.objects.filter(user__isnull=False).count()
    
    print("\n" + "="*60)
    print("📊 СТАТИСТИКА TELEGRAM ПОЛЬЗОВАТЕЛЕЙ")
    print("="*60)
    print(f"Всего пользователей в базе:        {total}")
    print(f"Нажали /start в боте:               {started}")
    print(f"С включенными уведомлениями:        {notifications_on}")
    print(f"Привязаны к аккаунтам Django:       {linked}")
    print("="*60 + "\n")
    
    if notifications_on > 0:
        print("👥 Пользователи с включенными уведомлениями:")
        for i, user in enumerate(TelegramUser.objects.filter(notifications_enabled=True, started_bot=True), 1):
            print(f"  {i}. {user.first_name} {user.last_name} (@{user.telegram_username or 'нет'}) - ID: {user.telegram_id}")
        print()
    
    return notifications_on


def send_to_all(message):
    """Отправить сообщение всем пользователям"""
    users = TelegramUser.objects.filter(
        notifications_enabled=True,
        started_bot=True
    )
    
    total = users.count()
    if total == 0:
        print("❌ Нет пользователей для отправки!")
        return
    
    print(f"\n📤 Начинаем отправку {total} пользователям...")
    print("-" * 60)
    
    success_count = 0
    failed_count = 0
    
    for user in users:
        print(f"Отправка {user.first_name} (@{user.telegram_username or 'нет'})...", end=" ")
        
        success = notification_service.send_message_sync(
            telegram_id=user.telegram_id,
            text=message
        )
        
        if success:
            print("✅")
            success_count += 1
        else:
            print("❌")
            failed_count += 1
    
    print("-" * 60)
    print(f"\n📊 Результаты:")
    print(f"  ✅ Успешно: {success_count}")
    print(f"  ❌ Ошибок:  {failed_count}")
    print(f"  📊 Всего:   {total}")
    print()


def send_to_one(telegram_id, message):
    """Отправить сообщение одному пользователю"""
    try:
        user = TelegramUser.objects.get(telegram_id=telegram_id)
        print(f"\n📤 Отправка сообщения пользователю {user.first_name} (@{user.telegram_username or 'нет'})...")
        
        success = notification_service.send_message_sync(
            telegram_id=telegram_id,
            text=message
        )
        
        if success:
            print("✅ Сообщение успешно отправлено!")
        else:
            print("❌ Ошибка отправки. Проверьте, запущен ли бот!")
    except TelegramUser.DoesNotExist:
        print(f"❌ Пользователь с ID {telegram_id} не найден!")


def main():
    """Главная функция"""
    print("\n" + "="*60)
    print("📬 ОТПРАВКА СООБЩЕНИЙ В TELEGRAM")
    print("="*60)
    
    # Показываем статистику
    recipients_count = show_statistics()
    
    if recipients_count == 0:
        print("⚠️  Нет пользователей для отправки!")
        print("   Попросите пользователей нажать /start в боте.")
        return
    
    while True:
        print("\n📋 Выберите действие:")
        print("1. Отправить сообщение ВСЕМ пользователям")
        print("2. Отправить тестовое сообщение одному пользователю")
        print("3. Показать статистику")
        print("4. Выход")
        
        choice = input("\nВаш выбор (1-4): ").strip()
        
        if choice == '1':
            print("\n💬 Введите текст сообщения (поддерживается Markdown):")
            print("   (для многострочного текста - введите END на новой строке)")
            
            lines = []
            while True:
                line = input()
                if line == 'END':
                    break
                lines.append(line)
            
            message = '\n'.join(lines)
            
            if not message.strip():
                print("❌ Пустое сообщение! Отмена.")
                continue
            
            print("\n📝 Вы собираетесь отправить:")
            print("-" * 60)
            print(message)
            print("-" * 60)
            
            confirm = input(f"\n⚠️  Отправить это сообщение {recipients_count} пользователям? (yes/no): ")
            
            if confirm.lower() == 'yes':
                send_to_all(message)
            else:
                print("❌ Отмена отправки.")
        
        elif choice == '2':
            users = list(TelegramUser.objects.filter(notifications_enabled=True, started_bot=True))
            
            if not users:
                print("❌ Нет пользователей!")
                continue
            
            print("\n👥 Доступные пользователи:")
            for i, user in enumerate(users, 1):
                print(f"  {i}. {user.first_name} {user.last_name} (@{user.telegram_username or 'нет'}) - ID: {user.telegram_id}")
            
            try:
                choice_num = int(input("\nВыберите номер пользователя: "))
                if 1 <= choice_num <= len(users):
                    selected_user = users[choice_num - 1]
                    
                    test_message = f"""
🧪 **Тестовое сообщение от TeacherHub**

Привет, {selected_user.first_name}!

Это тестовое сообщение для проверки работы системы уведомлений.

Если вы видите это - всё работает отлично! ✅

_Отправлено через send_telegram_message.py_
"""
                    
                    send_to_one(selected_user.telegram_id, test_message)
                else:
                    print("❌ Неверный номер!")
            except ValueError:
                print("❌ Введите число!")
        
        elif choice == '3':
            show_statistics()
        
        elif choice == '4':
            print("\n👋 До свидания!")
            break
        
        else:
            print("❌ Неверный выбор. Попробуйте снова.")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Прервано пользователем")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

