"""Smoke-тест Phase 9 — Прогресс ученика.

Сценарий:
  1. Покупка подписки 8 уроков.
  2. 3 урока completed, 1 no_show_student → attendance = 3/4 = 75%
  3. Создано 3 ДЗ: 2 graded (80, 100), 1 assigned
  4. Открыть /my/progress/ → видим 75%, avg 90, streak
  5. Открыть прогресс для учителя по этой подписке
"""
import os, sys, django, uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.test import Client

from teachers.models import (
    Booking, Subject, SubjectCategory, StudentProfile, TeacherProfile,
    TeacherSubject, TimeSlot,
)
from billing.models import (
    Homework, HomeworkAttachment, HomeworkSubmission, HomeworkSubmissionFile,
    Subscription, Tariff, Transaction, Wallet, WithdrawalRequest,
)
from billing.services import SubscriptionService, WalletService

User = get_user_model()
def green(s): return f'\033[32m{s}\033[0m'
def red(s):   return f'\033[31m{s}\033[0m'

SUFFIX = uuid.uuid4().hex[:6]
T_USER = f'p9_t_{SUFFIX}'
S_USER = f'p9_s_{SUFFIX}'

# Cleanup
old = User.objects.filter(username__startswith='p9_')
old_ids = list(old.values_list('pk', flat=True))
if old_ids:
    HomeworkSubmissionFile.objects.filter(submission__homework__teacher__user_id__in=old_ids).delete()
    HomeworkSubmission.objects.filter(homework__teacher__user_id__in=old_ids).delete()
    HomeworkAttachment.objects.filter(homework__teacher__user_id__in=old_ids).delete()
    Homework.objects.filter(teacher__user_id__in=old_ids).delete()
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

teacher_user = User.objects.create_user(
    username=T_USER, email=f'{T_USER}@x.com', password='Pass123', user_type='teacher',
)
teacher = TeacherProfile.objects.create(
    user=teacher_user, experience_years=3, moderation_status='approved', is_active=True,
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
WalletService.credit(user=student, amount=Decimal('1000000'),
                     tx_type=Transaction.Type.DEPOSIT, idempotency_key=f'p9-seed-{SUFFIX}')
tariff = Tariff.objects.create(
    teacher=teacher, subject=subject, name='Базовый',
    lessons_per_week=2, lesson_duration_minutes=60,
    duration_months=1, price_per_month=Decimal('800000'),
)
sub = SubscriptionService.purchase(
    student=student, tariff=tariff, idempotency_key=f'p9-sub-{SUFFIX}',
)

# 3 completed, 1 no_show
bookings = list(Booking.objects.filter(subscription=sub).order_by('slot__start_at'))
bookings[0].status = 'completed'; bookings[0].save()
bookings[1].status = 'completed'; bookings[1].save()
bookings[2].status = 'completed'; bookings[2].save()
bookings[3].status = 'no_show_student'; bookings[3].save()

# 3 ДЗ: 2 проверены, 1 ассигнованы
hw1 = Homework.objects.create(subscription=sub, teacher=teacher, student=student,
                              title='HW1', description='—', status=Homework.Status.GRADED)
HomeworkSubmission.objects.create(homework=hw1, student=student, grade=80)
hw2 = Homework.objects.create(subscription=sub, teacher=teacher, student=student,
                              title='HW2', description='—', status=Homework.Status.GRADED)
HomeworkSubmission.objects.create(homework=hw2, student=student, grade=100)
Homework.objects.create(subscription=sub, teacher=teacher, student=student,
                        title='HW3', description='—', status=Homework.Status.ASSIGNED)

sub.refresh_from_db()
print(f'→ Setup: 3 completed + 1 no_show + 3 HW (2 graded 80/100, 1 assigned)')
print(f'  attendance={sub.attendance_rate}%, avg_grade={sub.average_grade}, '
      f'completion={sub.homework_completion_rate}%')

# Student check
sc = Client(); sc.login(username=S_USER, password='Pass123')

r = sc.get('/ru/my/progress/')
print(f'[1] GET /my/progress/: {r.status_code}', green('OK') if r.status_code == 200 else red('FAIL'))
html = r.content.decode('utf-8')
print(f'[2] Заголовок «Мой прогресс»:',
      green('OK') if 'Мой прогресс' in html else red('FAIL'))
print(f'[3] Видна посещаемость 75%:',
      green('OK') if '75%' in html else red('FAIL'))
print(f'[4] Видна средняя оценка 90:',
      green('OK') if '90' in html else red('FAIL'))
print(f'[5] Видно «2/3» (HW progress):',
      green('OK') if '2/3' in html else red('FAIL'))

# Кнопка в профиле ученика
r = sc.get('/ru/profile/')
print(f'[6] Кнопка «Мой прогресс» в profile:',
      green('OK') if 'Мой прогресс' in r.content.decode('utf-8') else red('FAIL'))

# Teacher view
tc = Client(); tc.login(username=T_USER, password='Pass123')
r = tc.get(f'/ru/profile/student-progress/{sub.id}/')
print(f'[7] GET teacher_student_progress: {r.status_code}',
      green('OK') if r.status_code == 200 else red('FAIL'))
html = r.content.decode('utf-8')
print(f'[8] Видны: «Прогресс ученика» + посещаемость 75% + avg 90:',
      green('OK') if 'Прогресс ученика' in html and '75%' in html and '90' in html else red('FAIL'))
print(f'[9] Видна таблица «История уроков»:',
      green('OK') if 'История уроков' in html else red('FAIL'))
print(f'[10] Видна таблица ДЗ с оценками:',
      green('OK') if 'Домашние задания' in html and '80/100' in html else red('FAIL'))

# Кнопка «Открыть прогресс» на странице подписчиков
r = tc.get('/ru/profile/subscribers/')
print(f'[11] На /profile/subscribers/ кнопка «Открыть прогресс»:',
      green('OK') if 'Открыть прогресс' in r.content.decode('utf-8') else red('FAIL'))

# Чужой учитель не видит
other_user = User.objects.create_user(
    username=f'p9_other_{SUFFIX}', email=f'p9_other_{SUFFIX}@x.com',
    password='Pass123', user_type='teacher',
)
TeacherProfile.objects.create(user=other_user, experience_years=1, moderation_status='approved')
oc = Client(); oc.login(username=f'p9_other_{SUFFIX}', password='Pass123')
r = oc.get(f'/ru/profile/student-progress/{sub.id}/')
print(f'[12] Чужой учитель → 404:',
      green('OK') if r.status_code == 404 else red(f'got {r.status_code}'))

print()
print(green('✅ P9 smoke завершён.'))
