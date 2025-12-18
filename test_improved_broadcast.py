#!/usr/bin/env python
"""
Тест улучшенной массовой рассылки
"""
import os
import sys
import django

sys.path.append('/Users/humoyunswe/Desktop/ustozhubuz')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from teachers.models import TelegramUser, User
from teachers.admin_telegram_service import AdminTelegramService

def test_improved_broadcast():
    """Тест улучшенной устойчивой массовой рассылки"""
    
    print("🚀 ТЕСТ УЛУЧШЕННОЙ МАССОВОЙ РАССЫЛКИ")
    print("=" * 45)
    
    service = AdminTelegramService()
    
    if not service.bot:
        print("❌ Telegram bot не инициализирован")
        return
    
    # Статистика пользователей
    all_users = TelegramUser.objects.all()
    active_users = all_users.filter(started_bot=True, notifications_enabled=True)
    
    teachers = active_users.filter(user__user_type='teacher')
    students = active_users.filter(user__user_type='student')
    
    print(f"📊 СТАТИСТИКА:")
    print(f"   Всего пользователей: {all_users.count()}")
    print(f"   Активных: {active_users.count()}")
    print(f"   Учителей: {teachers.count()}")
    print(f"   Учеников: {students.count()}")
    
    if active_users.count() == 0:
        print("❌ Нет активных пользователей для тестирования")
        return
    
    # Тест 1: Общая рассылка
    print(f"\n🔄 ТЕСТ 1: Общая рассылка всем активным")
    try:
        stats = service.send_to_selected_users(
            telegram_users=list(active_users),
            message="🔔 Тестовое сообщение общей рассылки"
        )
        print(f"   Результат: ✅ {stats['success']}, ❌ {stats['failed']}, 📊 {stats['total']}")
        
        # Показываем типы ошибок
        if stats['error_summary']:
            print(f"   Ошибки:")
            for error_type, count in stats['error_summary'].items():
                if count > 0:
                    print(f"     - {error_type}: {count}")
    except Exception as e:
        print(f"   ❌ Ошибка теста: {e}")
    
    # Тест 2: Рассылка учителям
    if teachers.exists():
        print(f"\n👨‍🏫 ТЕСТ 2: Рассылка учителям")
        try:
            stats = service.send_to_teachers_only("Тестовое сообщение для учителей")
            print(f"   Результат: ✅ {stats['success']}, ❌ {stats['failed']}, 📊 {stats['total']}")
        except Exception as e:
            print(f"   ❌ Ошибка теста: {e}")
    else:
        print(f"\n👨‍🏫 ТЕСТ 2: Пропущен (нет учителей)")
    
    # Тест 3: Рассылка ученикам
    if students.exists():
        print(f"\n📚 ТЕСТ 3: Рассылка ученикам")
        try:
            stats = service.send_to_students_only("Тестовое сообщение для учеников")
            print(f"   Результат: ✅ {stats['success']}, ❌ {stats['failed']}, 📊 {stats['total']}")
        except Exception as e:
            print(f"   ❌ Ошибка теста: {e}")
    else:
        print(f"\n📚 ТЕСТ 3: Пропущен (нет учеников)")
    
    print(f"\n✅ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО!")

if __name__ == '__main__':
    test_improved_broadcast()