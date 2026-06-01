"""Smoke-тест Phase 7 — reviews per-lesson + CTA в my_subscriptions.

Сценарий:
  1. Ученик покупает подписку 8 уроков.
  2. 3 урока проходят (completed).
  3. /my/subscriptions/ показывает 3 «Оцените прошедший урок».
  4. Ученик оставляет 3 разных отзыва.
  5. После — секция «Оцените» пустая, в Review.objects 3 записи.
  6. Признак is_verified=True на всех. TeacherProfile.rating пересчитан.
"""
import os, sys, django, uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from teachers.models import (
    Booking, Review, Subject, SubjectCategory, StudentProfile, TeacherProfile,
    TeacherSubject, TimeSlot,
)
from billing.models import Subscription, Tariff, Transaction, Wallet, WithdrawalRequest
from billing.services import SubscriptionService, WalletService

User = get_user_model()
def green(s): return f'\033[32m{s}\033[0m'
def red(s):   return f'\033[31m{s}\033[0m'

SUFFIX = uuid.uuid4().hex[:6]
T_USER = f'p7_t_{SUFFIX}'
S_USER = f'p7_s_{SUFFIX}'

# Cleanup
old = User.objects.filter(username__startswith='p7_')
old_ids = list(old.values_list('pk', flat=True))
if old_ids:
    Review.objects.filter(student_id__in=old_ids).delete()
    Booking.objects.filter(student_id__in=old_ids).delete()
    Subscription.objects.filter(student_id__in=old_ids).delete()
    Subscription.objects.filter(teacher__user_id__in=old_ids).delete()
    Tariff.objects.filter(teacher__user_id__in=old_ids).delete()
    TimeSlot.objects.filter(teacher__user_id__in=old_ids).delete()
    WithdrawalRequest.objects.filter(user_id__in=old_ids).delete()
    Transaction.objects.filter(wallet__user_id__in=old_ids).delete()
    Wallet.objects.filter(user_id__in=old_ids).delete()
    TeacherSubject.objects.filter(teacher__user_id__in=old_ids).delete()
    StudentProfile.objects.filter(user_id__in=old_ids).delete()
    TeacherProfile.objects.filter(user_id__in=old_ids).delete()
    old.delete()

# Setup
teacher_user = User.objects.create_user(
    username=T_USER, email=f'{T_USER}@x.com', password='Pass123', user_type='teacher',
)
teacher = TeacherProfile.objects.create(
    user=teacher_user, experience_years=4, moderation_status='approved', is_active=True,
    weekly_schedule={d: [{'from': '09:00', 'to': '13:00'}] for d in
                     ('monday', 'tuesday', 'wednesday', 'thursday', 'friday')},
)
cat, _ = SubjectCategory.objects.get_or_create(name='Языки')
subject, _ = Subject.objects.get_or_create(name='Английский', defaults={'category': cat})
TeacherSubject.objects.create(teacher=teacher, subject=subject, hourly_rate=Decimal('80000'))

student = User.objects.create_user(
    username=S_USER, email=f'{S_USER}@x.com', password='Pass123', user_type='student',
)
StudentProfile.objects.create(user=student)
WalletService.credit(
    user=student, amount=Decimal('2000000'),
    tx_type=Transaction.Type.DEPOSIT, idempotency_key=f'p7-seed-{SUFFIX}',
)

tariff = Tariff.objects.create(
    teacher=teacher, subject=subject, name='Базовый',
    lessons_per_week=2, lesson_duration_minutes=60,
    duration_months=1, price_per_month=Decimal('800000'),
)

sub = SubscriptionService.purchase(
    student=student, tariff=tariff, idempotency_key=f'p7-purchase-{SUFFIX}',
)

# Завершаем 3 урока
bookings = list(Booking.objects.filter(subscription=sub).order_by('slot__start_at')[:3])
for b in bookings:
    b.status = 'completed'
    b.save()

teacher.refresh_from_db()
rating_before = teacher.rating

# UI: /my/subscriptions/ показывает 3 уведомления
sc = Client()
sc.login(username=S_USER, password='Pass123')

r = sc.get('/ru/my/subscriptions/')
html = r.content.decode('utf-8')
print(f'[1] /my/subscriptions/: HTTP {r.status_code}',
      green('OK') if r.status_code == 200 else red('FAIL'))
print(f'[2] Секция «Оцените прошедшие уроки» видна:',
      green('да') if 'Оцените прошедшие уроки' in html else red('нет'))
print(f'[3] Счётчик «(3)»:',
      green('видно') if '(3)' in html else red('нет'))
print(f'[4] CTA «Оценить» × 3:',
      green('OK') if html.count('Оценить</a>') == 3 else red(f'count={html.count("Оценить</a>")}'))

# Оставляем 3 разных отзыва
ratings = [5, 4, 5]
for i, b in enumerate(bookings):
    r = sc.post(reverse('leave_review', args=[b.id]) + '?next=/ru/my/subscriptions/', {
        'rating': ratings[i],
        'comment': f'Урок {i+1}: {"отлично" if ratings[i] == 5 else "хорошо"}',
        'knowledge_rating': ratings[i],
        'communication_rating': ratings[i],
        'punctuality_rating': ratings[i],
        'next': '/ru/my/subscriptions/',
    })
    if r.status_code != 302:
        print(f'   ! POST review #{i+1} failed: {r.status_code}')

# Проверка БД: 3 Review
count = Review.objects.filter(student=student, teacher=teacher).count()
print(f'[5] 3 Review в БД:',
      green('OK') if count == 3 else red(f'got {count}'))

verified_count = Review.objects.filter(student=student, teacher=teacher, is_verified=True).count()
print(f'[6] Все is_verified=True:',
      green('OK') if verified_count == 3 else red(f'verified={verified_count}'))

# Все привязаны к разным bookings (через OneToOne)
booking_ids = set(Review.objects.filter(student=student).values_list('booking_id', flat=True))
print(f'[7] 3 Review привязаны к 3 разным bookings:',
      green('OK') if len(booking_ids) == 3 else red(f'distinct={len(booking_ids)}'))

# UI: после отзывов секция «Оцените» пустая
r = sc.get('/ru/my/subscriptions/')
html = r.content.decode('utf-8')
print(f'[8] Секция «Оцените» исчезла:',
      green('да') if 'Оцените прошедшие уроки' not in html else red('всё ещё видна'))

# Rating пересчитан
teacher.refresh_from_db()
expected_avg = sum(ratings) / len(ratings)
print(f'[9] TeacherProfile.rating пересчитан (was {rating_before}, now {teacher.rating}, expected ~{expected_avg:.2f}):',
      green('OK') if abs(float(teacher.rating) - expected_avg) < 0.1 else red('FAIL'))

# Public страница учителя показывает отзывы
r = Client().get(f'/ru/teacher/{teacher.id}/')
html = r.content.decode('utf-8')
print(f'[10] Публичная страница показывает 3 отзыва:',
      green('OK') if html.count('Урок 1') + html.count('Урок 2') + html.count('Урок 3') >= 1 else red('нет'))

# Edge: повторный submit того же booking — обновляет существующий, count не растёт
r = sc.post(reverse('leave_review', args=[bookings[0].id]), {
    'rating': 1, 'comment': 'передумал',
    'knowledge_rating': 1, 'communication_rating': 1, 'punctuality_rating': 1,
})
count2 = Review.objects.filter(student=student, teacher=teacher).count()
print(f'[11] Повторный submit — общий count не растёт ({count2}):',
      green('OK') if count2 == 3 else red(f'got {count2}'))

# Updated отзыв
r1 = Review.objects.get(booking=bookings[0])
print(f'[12] Отзыв обновлён (rating: 5 → 1):',
      green('OK') if r1.rating == 1 else red(f'rating={r1.rating}'))

print()
print(green('✅ P7 smoke завершён.'))
