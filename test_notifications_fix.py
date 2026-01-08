#!/usr/bin/env python
"""
Тестовый скрипт для проверки системы уведомлений
"""
import os
import sys
import django

# Настройка Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from teachers.models import TeacherProfile, Notification, User

print("=" * 60)
print("🔍 ТЕСТ СИСТЕМЫ УВЕДОМЛЕНИЙ")
print("=" * 60)

# 1. Проверка структуры Notification
print("\n1️⃣ Проверка модели Notification:")
try:
    test_notif = Notification.objects.first()
    if test_notif:
        has_target_user = hasattr(test_notif, 'target_user')
        print(f"   ✅ Модель Notification существует")
        print(f"   ✅ Поле target_user: {'Есть' if has_target_user else 'ОТСУТСТВУЕТ!'}")
    else:
        print("   ⚠️ В базе нет уведомлений")
        # Создаём тестовое
        admin = User.objects.filter(is_staff=True).first()
        if admin:
            Notification.objects.create(
                title="Тест",
                short_text="Тест",
                full_text="Тест",
                created_by=admin
            )
            print("   ✅ Создано тестовое уведомление")
except Exception as e:
    print(f"   ❌ ОШИБКА: {e}")
    print("   💡 Нужно применить миграции: python manage.py migrate")

# 2. Проверка учителей на модерации
print("\n2️⃣ Учителя на модерации:")
pending = TeacherProfile.objects.filter(moderation_status='pending')
print(f"   Найдено: {pending.count()}")
if pending.count() == 0:
    print("   💡 Создайте тестового учителя или измените статус существующего")

# 3. Проверка админов
print("\n3️⃣ Администраторы:")
admins = User.objects.filter(is_staff=True)
print(f"   Найдено: {admins.count()}")
if admins.count() == 0:
    print("   ❌ Нет администраторов!")
else:
    admin = admins.first()
    print(f"   Админ для теста: {admin.username}")

# 4. ГЛАВНЫЙ ТЕСТ: одобрение учителя
print("\n4️⃣ ТЕСТ ОДОБРЕНИЯ:")
if pending.exists() and admins.exists():
    teacher = pending.first()
    admin = admins.first()
    
    print(f"   Учитель: {teacher.user.get_full_name() or teacher.user.username}")
    print(f"   ID: {teacher.id}")
    print(f"   User ID: {teacher.user.id}")
    
    # Считаем уведомления до
    before_count = Notification.objects.count()
    print(f"   Уведомлений ДО: {before_count}")
    
    try:
        # Вызываем approve
        print("\n   🚀 Вызываем teacher.approve()...")
        teacher.approve(moderator=admin, comment="Тестовое одобрение")
        
        # Проверяем результат
        after_count = Notification.objects.count()
        print(f"   Уведомлений ПОСЛЕ: {after_count}")
        
        if after_count > before_count:
            print(f"   ✅ УСПЕХ! Создано уведомлений: {after_count - before_count}")
            
            # Проверяем конкретное уведомление
            notif = Notification.objects.filter(
                target_user=teacher.user,
                title__contains="одобрен"
            ).order_by('-created_at').first()
            
            if notif:
                print(f"\n   📧 Созданное уведомление:")
                print(f"      Заголовок: {notif.title}")
                print(f"      Для: {notif.target_user.username}")
                print(f"      Target: {notif.target}")
                print(f"      Приоритет: {notif.priority}")
                print(f"      Активно: {notif.is_active}")
                
                # Проверяем видимость
                is_visible = notif.is_visible_for_user(teacher.user)
                print(f"      Видимо для учителя: {'✅ ДА' if is_visible else '❌ НЕТ'}")
            else:
                print("   ⚠️ Уведомление создано, но не найдено по фильтру")
        else:
            print("   ❌ Уведомление НЕ СОЗДАНО!")
            print("   💡 Проверьте логи сервера на ошибки")
            
    except Exception as e:
        print(f"   ❌ ОШИБКА при вызове approve(): {e}")
        import traceback
        print("\n   Детали ошибки:")
        traceback.print_exc()
else:
    print("   ⚠️ Нет данных для теста")
    if not pending.exists():
        print("   💡 Создайте учителя со статусом 'pending'")
    if not admins.exists():
        print("   💡 Создайте суперпользователя: python manage.py createsuperuser")

print("\n" + "=" * 60)
print("✅ ТЕСТ ЗАВЕРШЁН")
print("=" * 60)
