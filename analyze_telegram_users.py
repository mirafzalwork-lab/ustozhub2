#!/usr/bin/env python
"""
Анализ статуса всех Telegram пользователей
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

def analyze_telegram_users():
    """Анализирует всех Telegram пользователей"""
    
    all_users = TelegramUser.objects.all()
    
    print(f"📊 АНАЛИЗ TELEGRAM ПОЛЬЗОВАТЕЛЕЙ")
    print(f"=" * 50)
    
    # Общая статистика
    total = all_users.count()
    active = all_users.filter(started_bot=True, notifications_enabled=True).count()
    started_bot = all_users.filter(started_bot=True).count()
    notifications_on = all_users.filter(notifications_enabled=True).count()
    linked_to_django = all_users.filter(user__isnull=False).count()
    
    print(f"📈 ОБЩАЯ СТАТИСТИКА:")
    print(f"   Всего пользователей: {total}")
    print(f"   Готовы к рассылке: {active} ({round(active/total*100, 1) if total > 0 else 0}%)")
    print(f"   Запустили бота: {started_bot} ({round(started_bot/total*100, 1) if total > 0 else 0}%)")
    print(f"   Уведомления включены: {notifications_on} ({round(notifications_on/total*100, 1) if total > 0 else 0}%)")
    print(f"   Привязаны к аккаунтам: {linked_to_django} ({round(linked_to_django/total*100, 1) if total > 0 else 0}%)")
    
    # Анализ проблемных пользователей
    print(f"\n🚫 ПРИЧИНЫ ИСКЛЮЧЕНИЯ ИЗ РАССЫЛКИ:")
    
    not_started = all_users.filter(started_bot=False)
    print(f"   Не запустили бота: {not_started.count()}")
    if not_started.exists():
        print(f"      Примеры:")
        for user in not_started[:5]:
            print(f"      - {user.first_name} (@{user.telegram_username or 'нет'})")
        if not_started.count() > 5:
            print(f"      ... и еще {not_started.count() - 5}")
    
    notifications_off = all_users.filter(started_bot=True, notifications_enabled=False)
    print(f"   Отключили уведомления: {notifications_off.count()}")
    if notifications_off.exists():
        print(f"      Примеры:")
        for user in notifications_off[:5]:
            print(f"      - {user.first_name} (@{user.telegram_username or 'нет'})")
        if notifications_off.count() > 5:
            print(f"      ... и еще {notifications_off.count() - 5}")
    
    # Пользователи готовые к рассылке
    ready_users = all_users.filter(started_bot=True, notifications_enabled=True)
    print(f"\n✅ ГОТОВЫ К РАССЫЛКЕ ({ready_users.count()}):")
    for user in ready_users:
        status = "🔗 привязан" if user.user else "👤 не привязан"
        print(f"   - {user.first_name} (@{user.telegram_username or 'нет'}) - {status}")
    
    # Рекомендации
    print(f"\n💡 РЕКОМЕНДАЦИИ:")
    if not_started.count() > 0:
        print(f"   • Отправить напоминание {not_started.count()} пользователям запустить бота")
    if notifications_off.count() > 0:
        print(f"   • Предложить {notifications_off.count()} пользователям включить уведомления")
    
    missing_usernames = all_users.filter(telegram_username__isnull=True)
    if missing_usernames.exists():
        print(f"   • {missing_usernames.count()} пользователей без username - сложнее найти")

if __name__ == '__main__':
    try:
        analyze_telegram_users()
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        sys.exit(1)