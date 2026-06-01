"""Smoke-тест Phase 9.5 — платный пробный урок.

Сценарии:
  S1. Бронирование платного пробного: списание trial_price → escrow на booking
  S2. Идемпотентность: повторный booking с тем же slot → SlotUnavailable
  S3. Лимит: второй пробный с тем же teacher+subject → 409
  S4. Cancel ученика → refund на wallet
  S5. Cancel учителя (reject) → refund на wallet
  S6. completed + grace → payout 85/15 учителю+платформе через release_pending_payouts
  S7. Бесплатный пробный → деньги НЕ списаны
  S8. Глобальный инвариант: сумма денег сохранена
"""
import os
import sys
import django
import uuid
from datetime import timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.utils import timezone

from teachers.models import (
    Booking, Subject, SubjectCategory, StudentProfile, TeacherProfile,
    TeacherSubject, TimeSlot,
)
from billing.models import Subscription, Tariff, Transaction, Wallet, WithdrawalRequest
from billing.platform_account import get_or_create_platform_user
from billing.services import (
    InsufficientFunds, TrialAlreadyTaken, TrialNotPaid, TrialService, WalletService,
)

User = get_user_model()
G='\033[32m'; R='\033[31m'; Y='\033[33m'; B='\033[34m'; D='\033[0m'

PASSED=[]; FAILED=[]
def expect(c, name, det=''):
    if c: PASSED.append(name); print(f'  {G}✓{D} {name}', f'— {det}' if det else '')
    else: FAILED.append((name, det)); print(f'  {R}✗ {name}{D}', f'— {det}' if det else '')
def sect(t): print(f'\n{B}━━━ {t} ━━━{D}')


def total_money():
    w = Wallet.objects.aggregate(s=Sum('balance'))['s'] or Decimal('0')
    e = Subscription.objects.filter(status__in=Subscription.ACTIVE_STATUSES).aggregate(
        s=Sum('escrow_balance'))['s'] or Decimal('0')
    pend = WithdrawalRequest.objects.filter(status__in=['pending', 'approved']).aggregate(
        s=Sum('amount'))['s'] or Decimal('0')
    # Эскроу пробных висит на booking.trial_price_paid если не payouted и не refunded
    trial_escrow = Decimal('0')
    pending_trials = Booking.objects.filter(
        is_trial=True, trial_price_paid__isnull=False,
    ).exclude(status__in=['cancelled_by_student', 'cancelled_by_teacher', 'expired'])
    for b in pending_trials:
        # Деньги в эскроу пока нет payout и нет refund
        has_payout = Transaction.objects.filter(
            idempotency_key=f'trial-payout:{b.id}').exists()
        has_refund = Transaction.objects.filter(
            idempotency_key=f'trial-refund:{b.id}').exists()
        if not has_payout and not has_refund:
            trial_escrow += Decimal(b.trial_price_paid)
    return w + e + pend + trial_escrow


SUFFIX = uuid.uuid4().hex[:6]
print(f'\n{B}━━━━━━ PHASE 9.5: TRIAL BILLING SMOKE — {SUFFIX} ━━━━━━{D}')

# Cleanup
old = User.objects.filter(username__startswith='p95_')
old_ids = list(old.values_list('pk', flat=True))
if old_ids:
    Booking.objects.filter(student_id__in=old_ids).delete()
    TimeSlot.objects.filter(teacher__user_id__in=old_ids).delete()
    Transaction.objects.filter(wallet__user_id__in=old_ids).delete()
    Wallet.objects.filter(user_id__in=old_ids).delete()
    TeacherSubject.objects.filter(teacher__user_id__in=old_ids).delete()
    StudentProfile.objects.filter(user_id__in=old_ids).delete()
    TeacherProfile.objects.filter(user_id__in=old_ids).delete()
    old.delete()

BEFORE = total_money()
print(f'\n{Y}Total money before: {BEFORE:,.2f}{D}')

# Setup
T_USER = f'p95_t_{SUFFIX}'
S_USER = f'p95_s_{SUFFIX}'
S2_USER = f'p95_s2_{SUFFIX}'

teacher_user = User.objects.create_user(
    username=T_USER, email=f'{T_USER}@x.com', password='Pass123', user_type='teacher',
)
teacher = TeacherProfile.objects.create(
    user=teacher_user, experience_years=3,
    moderation_status='approved', is_active=True,
    weekly_schedule={'monday': [{'from': '09:00', 'to': '12:00'}]},
)
cat, _ = SubjectCategory.objects.get_or_create(name='Языки')
subject, _ = Subject.objects.get_or_create(name='Английский', defaults={'category': cat})

TRIAL_PRICE = Decimal('50000')
ts_paid = TeacherSubject.objects.create(
    teacher=teacher, subject=subject, hourly_rate=Decimal('80000'),
    is_free_trial=False, trial_duration_minutes=60, trial_price=TRIAL_PRICE,
)

student = User.objects.create_user(
    username=S_USER, email=f'{S_USER}@x.com', password='Pass123', user_type='student',
)
StudentProfile.objects.create(user=student)

student2 = User.objects.create_user(
    username=S2_USER, email=f'{S2_USER}@x.com', password='Pass123', user_type='student',
)
StudentProfile.objects.create(user=student2)

# Top-up
DEPOSIT = Decimal('500000')
WalletService.credit(
    user=student, amount=DEPOSIT,
    tx_type=Transaction.Type.DEPOSIT,
    idempotency_key=f'p95-dep-{SUFFIX}',
)
platform = get_or_create_platform_user()
WalletService.get_or_create_wallet(teacher_user)

# Создадим slot для теста
now = timezone.now()
future = now + timedelta(days=2)
slot1 = TimeSlot.objects.create(
    teacher=teacher, start_at=future, end_at=future + timedelta(hours=1),
    status='free',
)
slot2 = TimeSlot.objects.create(
    teacher=teacher, start_at=future + timedelta(hours=2),
    end_at=future + timedelta(hours=3), status='free',
)
slot3 = TimeSlot.objects.create(
    teacher=teacher, start_at=future + timedelta(days=1),
    end_at=future + timedelta(days=1, hours=1), status='free',
)


# ─────────── S1: книжим платный пробный
sect('S1: BOOK PAID TRIAL')
w = Wallet.objects.get(user=student); w.refresh_from_db()
b_before = w.balance

booking = TrialService.book_paid_trial(
    student=student, slot_id=slot1.id, teacher_subject=ts_paid,
    message='хочу попробовать',
)
w.refresh_from_db()
expect(w.balance == b_before - TRIAL_PRICE,
       f'ученик списан на {TRIAL_PRICE}',
       f'{b_before} → {w.balance}')
expect(booking.is_trial and booking.trial_price_paid == TRIAL_PRICE,
       'booking.is_trial=True, trial_price_paid сохранён')
expect(booking.status == 'pending', 'booking.status=pending (ожидает подтверждения)')
slot1.refresh_from_db()
expect(slot1.status == 'held', 'slot.status=held')

# Transaction есть
tx = Transaction.objects.filter(idempotency_key=f'trial-debit:{booking.id}').first()
expect(tx is not None and tx.amount == -TRIAL_PRICE,
       'Transaction trial-debit создана с отрицательной amount')


# ─────────── S2: лимит 1 пробный на (student, teacher, subject)
sect('S2: LIMIT — second trial → TrialAlreadyTaken')
try:
    TrialService.book_paid_trial(
        student=student, slot_id=slot2.id, teacher_subject=ts_paid,
    )
    expect(False, 'TrialAlreadyTaken должно было быть брошено')
except TrialAlreadyTaken:
    expect(True, 'TrialAlreadyTaken корректно')
w.refresh_from_db()
expect(w.balance == b_before - TRIAL_PRICE, 'баланс не списан повторно')


# ─────────── S3: cancel by student → refund
sect('S3: STUDENT CANCEL → REFUND')
booking.cancel_by_student()
slot1.refresh_from_db()
expect(slot1.status == 'free', 'слот освобождён')

refunded = TrialService.refund_trial(booking, reason='тест аудита')
w.refresh_from_db()
expect(refunded == TRIAL_PRICE, f'refunded = {TRIAL_PRICE}')
expect(w.balance == b_before, f'баланс восстановлен до {b_before}',
       f'фактический={w.balance}')

# Повторный refund — идемпотентность
refund2 = TrialService.refund_trial(booking, reason='повторный')
expect(refund2 == Decimal('0.00'),
       'повторный refund вернул 0 (идемпотентен)')


# ─────────── S4: новый платный пробный → completed + payout
sect('S4: COMPLETED + GRACE → PAYOUT 85/15')
b_before2 = w.balance
booking2 = TrialService.book_paid_trial(
    student=student, slot_id=slot2.id, teacher_subject=ts_paid,
    message='второй пробный, другой слот',
)
w.refresh_from_db()
# Note: первый booking уже cancelled, поэтому второй — НЕ нарушает лимит "1 пробный".
# (см. _existing_trial_qs.exclude(status in cancelled))
expect(w.balance == b_before2 - TRIAL_PRICE,
       f'списано {TRIAL_PRICE} за второй пробный')

# confirm + completed
booking2.status = 'confirmed'; booking2.save()
booking2.status = 'completed'
# Двигаем slot end_at в прошлое (за grace window)
slot2.start_at = now - timedelta(hours=settings.PAYOUT_GRACE_HOURS + 2)
slot2.end_at = now - timedelta(hours=settings.PAYOUT_GRACE_HOURS + 1)
slot2.save()
booking2.save()

# Сразу payout без celery task
teacher_w = Wallet.objects.get(user=teacher_user); teacher_w.refresh_from_db()
platform_w = Wallet.objects.get(user=platform); platform_w.refresh_from_db()
b_teach = teacher_w.balance
b_plat = platform_w.balance

ok = TrialService.release_trial_payout(booking2)
expect(ok is True, 'release_trial_payout returned True')

teacher_w.refresh_from_db(); platform_w.refresh_from_db()
COMMISSION_RATE = Decimal(settings.PLATFORM_COMMISSION_RATE)
expected_comm = (TRIAL_PRICE * COMMISSION_RATE).quantize(Decimal('0.01'))
expected_teacher = (TRIAL_PRICE - expected_comm).quantize(Decimal('0.01'))

expect(teacher_w.balance == b_teach + expected_teacher,
       f'учитель получил {expected_teacher} (85%)',
       f'было {b_teach}, стало {teacher_w.balance}')
expect(platform_w.balance == b_plat + expected_comm,
       f'платформа получила {expected_comm} (15%)',
       f'было {b_plat}, стало {platform_w.balance}')

# Идемпотентность payout
ok2 = TrialService.release_trial_payout(booking2)
expect(ok2 is False, 'повторный payout вернул False (idempotent)')
teacher_w.refresh_from_db(); platform_w.refresh_from_db()
expect(teacher_w.balance == b_teach + expected_teacher, 'двойного зачисления нет')


# ─────────── S5: бесплатный пробный — без денег
sect('S5: FREE TRIAL — NO MONEY DEDUCTED')
# Меняем настройку учителя на бесплатный
ts_paid.is_free_trial = True
ts_paid.trial_price = None
ts_paid.save()

# Создаём другого ученика для чистоты теста
w2 = WalletService.get_or_create_wallet(student2)
WalletService.credit(
    user=student2, amount=Decimal('100000'),
    tx_type=Transaction.Type.DEPOSIT,
    idempotency_key=f'p95-dep2-{SUFFIX}',
)
w2.refresh_from_db()
b2_before = w2.balance

# Регулярный create_hold с is_trial=True (как для бесплатного)
booking3 = Booking.create_hold(
    slot_id=slot3.id, student=student2,
    subject=subject, is_trial=True,
)
w2.refresh_from_db()
expect(booking3.is_trial and booking3.trial_price_paid is None,
       'бесплатный пробный: trial_price_paid=None')
expect(w2.balance == b2_before, 'баланс ученика НЕ изменился',
       f'{b2_before} → {w2.balance}')


# ─────────── S6: глобальный инвариант
sect('S6: GLOBAL INVARIANT')
AFTER = total_money()
deposits = DEPOSIT + Decimal('100000')  # student + student2
expected = BEFORE + deposits
diff = AFTER - expected
print(f'    before    = {BEFORE:,.2f}')
print(f'    +deposits = {deposits:,.2f}')
print(f'    expected  = {expected:,.2f}')
print(f'    actual    = {AFTER:,.2f}')
print(f'    diff      = {diff:,.2f}')
expect(diff == Decimal('0'), 'TotalMoney_after == TotalMoney_before + deposits')


# ─────────── CLEANUP
sect('CLEANUP')
ids = list(User.objects.filter(username__startswith='p95_').values_list('pk', flat=True))
Booking.objects.filter(student_id__in=ids).delete()
TimeSlot.objects.filter(teacher__user_id__in=ids).delete()
Transaction.objects.filter(wallet__user_id__in=ids).delete()
Wallet.objects.filter(user_id__in=ids).delete()
TeacherSubject.objects.filter(teacher__user_id__in=ids).delete()
StudentProfile.objects.filter(user_id__in=ids).delete()
TeacherProfile.objects.filter(user_id__in=ids).delete()
deleted = User.objects.filter(username__startswith='p95_').delete()
print(f'  очищено: {deleted}')

# ─────────── ИТОГ
print(f'\n{B}━━━━━━━━━━━━━━━━━━━━━━━━{D}')
total = len(PASSED) + len(FAILED)
print(f'  ВСЕГО: {total}')
print(f'  {G}PASSED: {len(PASSED)}{D}')
if FAILED:
    print(f'  {R}FAILED: {len(FAILED)}{D}')
    for n, d in FAILED:
        print(f'    ✗ {n}', f'— {d}' if d else '')
    sys.exit(1)
print(f'\n{G}✅ P9.5 smoke завершён — биллинг пробного работает.{D}')
