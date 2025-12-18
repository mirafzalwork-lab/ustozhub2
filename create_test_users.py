#!/usr/bin/env python
"""
Создание тестовых пользователей для проверки рассылки
"""
import os
import sys
import django

sys.path.append('/Users/humoyunswe/Desktop/ustozhubuz')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from teachers.models import TelegramUser, User

def create_test_users():
    """Создает тестовых пользователей"""
    
    print("👥 СОЗДАНИЕ ТЕСТОВЫХ ПОЛЬЗОВАТЕЛЕЙ")
    print("=" * 35)
    
    # Создаем Django пользователей
    teacher_user, created = User.objects.get_or_create(
        username='test_teacher',
        defaults={
            'first_name': 'Алексей',
            'last_name': 'Учителев',
            'user_type': 'teacher',
            'email': 'teacher@test.com'
        }
    )
    if created:
        teacher_user.set_password('testpass123')
        teacher_user.save()
        print(f"✅ Создан учитель: {teacher_user.get_full_name()}")
    
    student_user, created = User.objects.get_or_create(
        username='test_student',
        defaults={
            'first_name': 'Мария',
            'last_name': 'Ученица',
            'user_type': 'student',
            'email': 'student@test.com'
        }
    )
    if created:
        student_user.set_password('testpass123')
        student_user.save()
        print(f"✅ Создан ученик: {student_user.get_full_name()}")
    
    # Создаем Telegram пользователей
    teacher_tg, created = TelegramUser.objects.get_or_create(
        telegram_username='test_teacher_tg',
        defaults={
            'user': teacher_user,
            'telegram_id': 2000000001,
            'first_name': 'Алексей',
            'last_name': 'Учителев',
            'started_bot': True,
            'notifications_enabled': True
        }
    )
    if created:
        print(f"✅ Создан Telegram учитель: @{teacher_tg.telegram_username}")
    
    student_tg, created = TelegramUser.objects.get_or_create(
        telegram_username='test_student_tg',
        defaults={
            'user': student_user,
            'telegram_id': 2000000002,
            'first_name': 'Мария',
            'last_name': 'Ученица',
            'started_bot': True,
            'notifications_enabled': True
        }
    )
    if created:
        print(f"✅ Создан Telegram ученик: @{student_tg.telegram_username}")
    
    # Статистика
    total = TelegramUser.objects.count()
    active = TelegramUser.objects.filter(started_bot=True, notifications_enabled=True).count()
    teachers = TelegramUser.objects.filter(user__user_type='teacher', started_bot=True).count()
    students = TelegramUser.objects.filter(user__user_type='student', started_bot=True).count()
    
    print(f"\n📊 ИТОГОВАЯ СТАТИСТИКА:")
    print(f"   Всего Telegram пользователей: {total}")
    print(f"   Активных: {active}")
    print(f"   Учителей: {teachers}")
    print(f"   Учеников: {students}")

if __name__ == '__main__':
    create_test_users()