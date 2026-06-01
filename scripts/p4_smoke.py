"""Smoke-тест Phase 4 — payout flow.

Запускает реальную покупку, помечает 3 урока completed, бэкдейтит slot.end_at
так, чтобы они прошли grace window, и запускает release_pending_payouts.
Проверяет балансы учителя/платформы/эскроу + UI.
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
from billing.tasks import release_pending_payouts

User = get_user_model()

def green(s): return f'\033[32m{s}\033[0m'
def red(s):   return f'\033[31m{s}\033[0m'

SUFFIX = uuid.uuid4().hex[:6]
T_USER = f'p4_teacher_{SUFFIX}'
S_USER = f'p4_student_{SUFFIX}'

# Очистка
old = User.objects.filter(username__startswith='p4_')
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

# Setup
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
    idempotency_key=f'p4-seed-{SUFFIX}',
)

tariff = Tariff.objects.create(
    teacher=teacher, subject=subject,
    name='Базовый', lessons_per_week=2, lesson_duration_minutes=60,
    duration_months=1, price_per_month=Decimal('800000'),
)

sub = SubscriptionService.purchase(
    student=student, tariff=tariff, idempotency_key=f'p4-purchase-{SUFFIX}',
)
print(f'→ Subscription: total={sub.total_lessons} escrow={sub.escrow_balance} '
      f'price_per_lesson={sub.price_per_lesson} commission={sub.commission_rate}')

# Помечаем 3 первых booking как completed и бэкдейтим slot.end_at так,
# чтобы они «прошли» grace window.
bookings = list(Booking.objects.filter(subscription=sub).order_by('slot__start_at')[:3])
past = timezone.now() - timedelta(hours=settings.PAYOUT_GRACE_HOURS + 1)
for b in bookings:
    b.status = 'completed'
    b.save()
    b.slot.start_at = past - timedelta(hours=1)
    b.slot.end_at = past
    b.slot.save()

sub.refresh_from_db()
print(f'[1] После signal completed: completed_lessons={sub.completed_lessons} '
      f'(ожидаем 3)',
      green('OK') if sub.completed_lessons == 3 else red('FAIL'))

# Запускаем task
result = release_pending_payouts()
print(f'[2] release_pending_payouts: {result}')

sub.refresh_from_db()
teacher_user.wallet.refresh_from_db()
platform.wallet.refresh_from_db()

# 3 урока × 100000/урок × 0.85 = 255000 учителю
expected_teacher = Decimal('255000.00')
# 3 × 100000 × 0.15 = 45000 платформе
expected_platform = platform_start + Decimal('45000.00')
expected_escrow = Decimal('500000.00')

print(f'[3] teacher.balance={teacher_user.wallet.balance} (ожидаем {expected_teacher})',
      green('OK') if teacher_user.wallet.balance == expected_teacher else red('FAIL'))
print(f'[4] platform.balance={platform.wallet.balance} (ожидаем {expected_platform})',
      green('OK') if platform.wallet.balance == expected_platform else red('FAIL'))
print(f'[5] escrow={sub.escrow_balance} (ожидаем {expected_escrow})',
      green('OK') if sub.escrow_balance == expected_escrow else red('FAIL'))
print(f'[6] lessons_paid_out={sub.lessons_paid_out} (ожидаем 3)',
      green('OK') if sub.lessons_paid_out == 3 else red('FAIL'))

# Идемпотентность: повторный прогон не должен ничего сделать.
result2 = release_pending_payouts()
print(f'[7] повторный прогон task: paid={result2["paid"]} (ожидаем 0)',
      green('OK') if result2['paid'] == 0 else red('FAIL'))

# UI проверка: учитель видит доход
tc = Client()
tc.login(username=T_USER, password='Password123')
r = tc.get('/ru/profile/subscribers/')
shows_total = b'255 000' in r.content.replace(b'\xc2\xa0', b' ')
shows_total_alt = b'255000' in r.content
shows_subs = b'p4_student' in r.content
print(f'[8] UI subscribers: HTTP {r.status_code}', green('OK') if r.status_code == 200 else red('FAIL'))
print(f'   → видна сумма 255 000:', green('да') if (shows_total or shows_total_alt) else red('нет'))
print(f'   → виден ученик:', green('да') if shows_subs else red('нет'))

# Ученик видит "выплачено учителю"
sc = Client()
sc.login(username=S_USER, password='Password123')
r = sc.get('/ru/my/subscriptions/')
print(f'[9] UI my_subscriptions: HTTP {r.status_code}', green('OK') if r.status_code == 200 else red('FAIL'))
shows_paid = b'255 000' in r.content.replace(b'\xc2\xa0', b' ') or b'255000' in r.content
print(f'   → видна сумма выплат 255 000:', green('да') if shows_paid else red('нет'))

print()
print(green('✅ P4 smoke завершён.'))
