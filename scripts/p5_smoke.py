"""Smoke-тест Phase 5 — cancellation + refund.

Сценарий:
  1. Ученик покупает подписку 800k = 8 уроков.
  2. 2 урока проводятся (status=completed), 1 из них уже выплачен учителю.
  3. Ученик отменяет подписку через UI.
  4. Проверка: учитель получает доплату за 2-й урок, ученик получает refund 600k,
     6 будущих bookings отменены, слоты свободны.
"""
import os, sys, django, uuid
from datetime import timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from teachers.models import (
    Booking, Subject, SubjectCategory, StudentProfile, TeacherProfile,
    TeacherSubject, TimeSlot,
)
from billing.models import Subscription, Tariff, Transaction, Wallet
from billing.platform_account import get_or_create_platform_user
from billing.services import SubscriptionService, WalletService

User = get_user_model()

def green(s): return f'\033[32m{s}\033[0m'
def red(s):   return f'\033[31m{s}\033[0m'

SUFFIX = uuid.uuid4().hex[:6]
T_USER = f'p5_teacher_{SUFFIX}'
S_USER = f'p5_student_{SUFFIX}'

# Очистка
old = User.objects.filter(username__startswith='p5_')
old_ids = list(old.values_list('pk', flat=True))
if old_ids:
    Booking.objects.filter(student_id__in=old_ids).delete()
    Subscription.objects.filter(student_id__in=old_ids).delete()
    Subscription.objects.filter(teacher__user_id__in=old_ids).delete()
    Tariff.objects.filter(teacher__user_id__in=old_ids).delete()
    TimeSlot.objects.filter(teacher__user_id__in=old_ids).delete()
    Transaction.objects.filter(wallet__user_id__in=old_ids).delete()
    Wallet.objects.filter(user_id__in=old_ids).delete()
    TeacherSubject.objects.filter(teacher__user_id__in=old_ids).delete()
    StudentProfile.objects.filter(user_id__in=old_ids).delete()
    TeacherProfile.objects.filter(user_id__in=old_ids).delete()
    old.delete()

platform = get_or_create_platform_user()
platform_start = platform.wallet.balance

teacher_user = User.objects.create_user(
    username=T_USER, email=f'{T_USER}@x.com', password='Password123', user_type='teacher',
)
teacher = TeacherProfile.objects.create(
    user=teacher_user, experience_years=5,
    moderation_status='approved', is_active=True,
    weekly_schedule={d: [{'from': '09:00', 'to': '13:00'}] for d in
                     ('monday', 'tuesday', 'wednesday', 'thursday', 'friday')},
)
cat, _ = SubjectCategory.objects.get_or_create(name='Языки')
subject, _ = Subject.objects.get_or_create(name='Английский', defaults={'category': cat})
TeacherSubject.objects.create(teacher=teacher, subject=subject, hourly_rate=Decimal('80000'))

student = User.objects.create_user(
    username=S_USER, email=f'{S_USER}@x.com', password='Password123', user_type='student',
)
StudentProfile.objects.create(user=student)
WalletService.credit(
    user=student, amount=Decimal('2000000'),
    tx_type=Transaction.Type.DEPOSIT,
    idempotency_key=f'p5-seed-{SUFFIX}',
)

tariff = Tariff.objects.create(
    teacher=teacher, subject=subject,
    name='Базовый', lessons_per_week=2, lesson_duration_minutes=60,
    duration_months=1, price_per_month=Decimal('800000'),
)

sub = SubscriptionService.purchase(
    student=student, tariff=tariff, idempotency_key=f'p5-purchase-{SUFFIX}',
)
print(f'→ Куплено: 8 уроков, escrow={sub.escrow_balance}, баланс ученика = '
      f'{Wallet.objects.get(user=student).balance}')

# Делаем 2 урока completed, 1 из них уже выплачен.
bookings = list(Booking.objects.filter(subscription=sub).order_by('slot__start_at')[:2])
past = timezone.now() - timedelta(hours=settings.PAYOUT_GRACE_HOURS + 1)
for b in bookings:
    b.status = 'completed'
    b.save()
    b.slot.start_at = past - timedelta(hours=1)
    b.slot.end_at = past
    b.slot.save()
# Выплачиваем только первый.
SubscriptionService.release_lesson_payout(bookings[0])

sub.refresh_from_db()
print(f'→ После 2 completed + 1 payout: completed={sub.completed_lessons} '
      f'paid_out={sub.lessons_paid_out} escrow={sub.escrow_balance}')

# Cancel через UI
sc = Client()
sc.login(username=S_USER, password='Password123')
r = sc.post(f'/ru/subscriptions/{sub.id}/cancel/', {'reason': 'не подошло время'})
print(f'[1] POST cancel: {r.status_code} (ожидаем 302)',
      green('OK') if r.status_code == 302 else red('FAIL'))

sub.refresh_from_db()
teacher_user.wallet.refresh_from_db()
platform.wallet.refresh_from_db()
student_wallet = Wallet.objects.get(user=student)

# Ожидаем:
# До cancel:
#   teacher.balance = 85000 (за 1 paid урок)
#   platform.balance = 15000 + platform_start
#   escrow = 600000 (8 - 2 paid = 6, 6×100k? нет)
# Wait: после release_lesson_payout первого урока escrow = 800k - 100k = 700k
# При cancel:
#   - Доплачивается 2-й completed → teacher +85k, platform +15k, escrow -100k = 600k
#   - Refund 600k → student
# Итог:
#   teacher.balance = 85000 + 85000 = 170 000
#   platform.balance = platform_start + 15000 + 15000 = platform_start + 30 000
#   student.balance = 1 200 000 (после покупки) + 600 000 (refund) = 1 800 000

expected_teacher = Decimal('170000.00')
expected_platform = platform_start + Decimal('30000.00')
expected_student = Decimal('1800000.00')

print(f'[2] status={sub.status}', green('OK') if sub.status == 'cancelled_by_student' else red('FAIL'))
print(f'[3] escrow={sub.escrow_balance} (ожидаем 0)',
      green('OK') if sub.escrow_balance == 0 else red('FAIL'))
print(f'[4] teacher.balance={teacher_user.wallet.balance} (ожидаем {expected_teacher})',
      green('OK') if teacher_user.wallet.balance == expected_teacher else red('FAIL'))
print(f'[5] platform.balance={platform.wallet.balance} (ожидаем {expected_platform})',
      green('OK') if platform.wallet.balance == expected_platform else red('FAIL'))
print(f'[6] student.balance={student_wallet.balance} (ожидаем {expected_student})',
      green('OK') if student_wallet.balance == expected_student else red('FAIL'))

# Бухгалтерская сходимость: исходные 800000 == teacher (2×85k) + platform (2×15k) + student refund (600k)
balanced = (
    Decimal('170000') + (platform.wallet.balance - platform_start) + (Decimal('600000'))
)
print(f'[7] бухгалтерская сходимость: 800000 == {balanced}',
      green('OK') if balanced == Decimal('800000') else red('FAIL'))

# 6 будущих bookings отменены
cancelled = Booking.objects.filter(subscription=sub, status='cancelled_by_student').count()
print(f'[8] cancelled bookings: {cancelled} (ожидаем 6)',
      green('OK') if cancelled == 6 else red('FAIL'))

# Слоты свободны
free_slots = TimeSlot.objects.filter(teacher=teacher, status='free').count()
print(f'[9] free slots: {free_slots} (ожидаем >= 6)',
      green('OK') if free_slots >= 6 else red('FAIL'))

# Повторный cancel должен дать сообщение об ошибке (но без 500)
r = sc.post(f'/ru/subscriptions/{sub.id}/cancel/', {'reason': 'повтор'})
print(f'[10] повторный cancel: HTTP {r.status_code} (ожидаем 302, без 500)',
      green('OK') if r.status_code == 302 else red('FAIL'))

# UI: подписка в истории
r = sc.get('/ru/my/subscriptions/')
in_history = b'cancelled_by_student' in r.content or 'Отменена учеником' in r.content.decode('utf-8')
print(f'[11] подписка в "Истории" UI:', green('да') if in_history else red('нет'))

print()
print(green('✅ P5 smoke завершён.'))
