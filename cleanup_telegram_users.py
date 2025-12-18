#!/usr/bin/env python
"""
Скрипт для анализа и очистки неактивных Telegram пользователей на продакшене
"""
import os
import sys
import django
from datetime import datetime, timedelta

# Добавляем путь к проекту
sys.path.append('/Users/humoyunswe/Desktop/ustozhubuz')

# Настраиваем Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from teachers.models import TelegramUser
from django.utils import timezone

def analyze_production_users():
    """Анализ пользователей для продакшн среды"""
    
    print(f"🏭 АНАЛИЗ ПРОДАКШН TELEGRAM ПОЛЬЗОВАТЕЛЕЙ")
    print(f"=" * 60)
    
    # Общая статистика
    all_users = TelegramUser.objects.all()
    total = all_users.count()
    
    # Активные пользователи (готовы к рассылке)
    active = all_users.filter(started_bot=True, notifications_enabled=True)
    active_count = active.count()
    
    # Различные категории неактивных
    not_started = all_users.filter(started_bot=False)
    notifications_off = all_users.filter(notifications_enabled=False)
    
    print(f"📊 ОБЩАЯ СТАТИСТИКА:")
    print(f"   Всего пользователей: {total:,}")
    print(f"   Готовы к рассылке: {active_count:,} ({round(active_count/total*100, 1) if total > 0 else 0}%)")
    print(f"   Проблемных: {total - active_count:,} ({round((total - active_count)/total*100, 1) if total > 0 else 0}%)")
    
    print(f"\n🚫 КАТЕГОРИИ ПРОБЛЕМНЫХ ПОЛЬЗОВАТЕЛЕЙ:")
    print(f"   Не запустили бота: {not_started.count():,}")
    print(f"   Отключили уведомления: {notifications_off.count():,}")
    
    # Анализ по дате создания
    week_ago = timezone.now() - timedelta(days=7)
    month_ago = timezone.now() - timedelta(days=30)
    
    recent_inactive = not_started.filter(created_at__gte=week_ago).count()
    old_inactive = not_started.filter(created_at__lt=month_ago).count()
    
    print(f"\n📅 АНАЛИЗ ПО ВРЕМЕНИ:")
    print(f"   Новые неактивные (< 7 дней): {recent_inactive:,}")
    print(f"   Старые неактивные (> 30 дней): {old_inactive:,}")
    
    # Рекомендации
    print(f"\n💡 РЕКОМЕНДАЦИИ:")
    print(f"   1. Удалить старых неактивных пользователей (> 30 дней): {old_inactive:,}")
    print(f"   2. Отправить напоминание новым неактивным: {recent_inactive:,}")
    print(f"   3. Проверить работу бота и процесс регистрации")
    
    # Предложение очистки
    if old_inactive > 0:
        print(f"\n🧹 ОЧИСТКА БАЗЫ ДАННЫХ:")
        print(f"   Можно безопасно удалить {old_inactive:,} старых неактивных пользователей")
        
        response = input(f"\nВыполнить очистку? (y/N): ").strip().lower()
        if response == 'y':
            deleted_count = not_started.filter(created_at__lt=month_ago).delete()[0]
            print(f"✅ Удалено {deleted_count:,} неактивных пользователей")
            
            # Повторная статистика
            new_total = TelegramUser.objects.all().count()
            new_active = TelegramUser.objects.filter(started_bot=True, notifications_enabled=True).count()
            print(f"📊 После очистки: {new_active:,} активных / {new_total:,} всего ({round(new_active/new_total*100, 1)}%)")
        else:
            print("❌ Очистка отменена")

def find_duplicate_users():
    """Поиск дублированных пользователей"""
    print(f"\n🔍 ПОИСК ДУБЛИКАТОВ:")
    
    # Поиск по telegram_id
    from django.db.models import Count
    duplicates = TelegramUser.objects.values('telegram_id').annotate(
        count=Count('telegram_id')
    ).filter(count__gt=1)
    
    if duplicates.exists():
        print(f"   Найдено дубликатов по telegram_id: {duplicates.count()}")
        for dup in duplicates[:5]:
            users = TelegramUser.objects.filter(telegram_id=dup['telegram_id'])
            print(f"   - ID {dup['telegram_id']}: {dup['count']} записей")
    else:
        print(f"   ✅ Дубликатов по telegram_id не найдено")

def check_bot_blockers():
    """Проверяем пользователей, которые могли заблокировать бота"""
    print(f"\n🚫 АНАЛИЗ ЗАБЛОКИРОВАВШИХ БОТА:")
    
    # Пользователи, которые были активны, но давно не взаимодействовали
    month_ago = timezone.now() - timedelta(days=30)
    potentially_blocked = TelegramUser.objects.filter(
        started_bot=True,
        notifications_enabled=True,
        last_interaction__lt=month_ago
    )
    
    print(f"   Потенциально заблокировали бота: {potentially_blocked.count():,}")
    print(f"   (активные, но без активности > 30 дней)")

if __name__ == '__main__':
    try:
        analyze_production_users()
        find_duplicate_users()
        check_bot_blockers()
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)