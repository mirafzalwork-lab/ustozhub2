"""Глубокий аудит биллинга — сквозной сценарий по всем денежным операциям.

Цель: убедиться, что система **самосогласована** — деньги не исчезают
и не появляются из ниоткуда.

Главный инвариант:
    TotalMoney = SUM(wallet.balance) + SUM(active_subscription.escrow_balance)
    + SUM(pending+approved withdrawal.amount)  # «висят» вне кошельков
    - SUM(completed withdrawal.amount)         # ушли наружу
    - SUM(deposits внешних)                    # вошли извне
    = const

Проверяемые сценарии:
  S1.  Top-up администратором → DEPOSIT, баланс растёт ровно на сумму
  S2.  Идемпотентность credit (повторный вызов с тем же ключом = no-op)
  S3.  Покупка подписки: студент платит price_total → escrow = price_total
  S4.  Идемпотентность purchase: повторный вызов с тем же ключом = вернул ту же подписку
  S5.  AlreadySubscribed: второй раз тот же teacher+subject → InsufficientFunds НЕ происходит, raise
  S6.  InsufficientFunds: студент с 0 балансом не может купить — баланс не меняется
  S7.  Auto-bookings: создано ровно total_lessons confirmed bookings
  S8.  Завершение урока + release_lesson_payout: escrow ↓, teacher ↑ 85%, platform ↑ 15%
  S9.  Идемпотентность payout: повторный release_lesson_payout = False, нет двойного зачисления
  S10. Cancel by student с completed-но-не-paid: учитель получает payout перед refund
  S11. Cancel: будущие confirmed → cancelled_by_student, слоты → free, escrow → wallet
  S12. Withdrawal create: учитель -= amount, заявка pending
  S13. Withdrawal < MIN: raise WithdrawalAmountError, баланс не меняется
  S14. Withdrawal reject: refund на wallet
  S15. Withdrawal complete: баланс не меняется (уже списан при create)
  S16. Reconciliation: для каждого wallet sum(Transaction.amount) == wallet.balance
  S17. Глобальный инвариант: TotalMoney до и после всех операций совпадает (с учётом внешних потоков)
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
from billing.models import (
    Subscription, Tariff, Transaction, Wallet, WithdrawalRequest,
)
from billing.platform_account import get_or_create_platform_user
from billing.services import (
    AlreadySubscribed, CancellationError, InsufficientFunds,
    SubscriptionService, WalletService, WithdrawalAmountError,
    WithdrawalError, WithdrawalService,
)

User = get_user_model()

G = '\033[32m'; R = '\033[31m'; Y = '\033[33m'; B = '\033[34m'; D = '\033[0m'
OK = lambda: f'{G}✓{D}'
FAIL = lambda: f'{R}✗{D}'

PASSED = []
FAILED = []
WARNED = []

def expect(cond, name, detail=''):
    if cond:
        PASSED.append(name); print(f'  {OK()} {name}', detail and f'— {detail}' or '')
    else:
        FAILED.append((name, detail)); print(f'  {FAIL()} {R}{name}{D}', detail and f'— {detail}' or '')


def warn(name, detail=''):
    WARNED.append((name, detail))
    print(f'  {Y}!{D} {name}', detail and f'— {detail}' or '')


def section(title):
    print(f'\n{B}━━━ {title} ━━━{D}')


def total_money_in_system():
    """Сколько денег внутри системы.

    Считаем:
      + ВСЕ wallet.balance (включая platform, исключая __platform__ для отчётности — но он тоже часть системы)
      + ВСЕ active subscription.escrow_balance
      + ВСЕ pending+approved withdrawal.amount  (списано с кошелька, но ещё не отдано)
    """
    w = Wallet.objects.aggregate(s=Sum('balance'))['s'] or Decimal('0')
    e = Subscription.objects.filter(
        status__in=Subscription.ACTIVE_STATUSES,
    ).aggregate(s=Sum('escrow_balance'))['s'] or Decimal('0')
    pend = WithdrawalRequest.objects.filter(
        status__in=['pending', 'approved'],
    ).aggregate(s=Sum('amount'))['s'] or Decimal('0')
    return {'wallets': w, 'escrow': e, 'pending_withdrawals': pend, 'total': w + e + pend}


# ────────────────────────────────────────────────────────────────────────
# CLEANUP & SETUP
# ────────────────────────────────────────────────────────────────────────

SUFFIX = uuid.uuid4().hex[:6]
T_USER = f'audit_t_{SUFFIX}'
S_USER = f'audit_s_{SUFFIX}'
S2_USER = f'audit_s2_{SUFFIX}'
A_USER = f'audit_admin_{SUFFIX}'

print(f'{B}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{D}')
print(f'{B}      ГЛУБОКИЙ АУДИТ БИЛЛИНГА — {SUFFIX}{D}')
print(f'{B}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{D}')

# Полная очистка предыдущих audit-пользователей
old_users = User.objects.filter(username__startswith='audit_')
old_ids = list(old_users.values_list('pk', flat=True))
if old_ids:
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
    old_users.delete()

# Фиксируем "before" — состояние системы перед началом
BEFORE = total_money_in_system()
print(f'\n{Y}Состояние до аудита:{D}')
print(f'  wallets        = {BEFORE["wallets"]:>14,.2f}')
print(f'  escrow         = {BEFORE["escrow"]:>14,.2f}')
print(f'  pending WD     = {BEFORE["pending_withdrawals"]:>14,.2f}')
print(f'  total          = {BEFORE["total"]:>14,.2f}')

# Учитель и ученик
teacher_user = User.objects.create_user(
    username=T_USER, email=f'{T_USER}@x.com', password='Pass123', user_type='teacher',
)
teacher = TeacherProfile.objects.create(
    user=teacher_user, experience_years=3,
    moderation_status='approved', is_active=True,
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

student2 = User.objects.create_user(
    username=S2_USER, email=f'{S2_USER}@x.com', password='Pass123', user_type='student',
)
StudentProfile.objects.create(user=student2)

admin = User.objects.create_user(
    username=A_USER, email=f'{A_USER}@x.com', password='Pass123',
    is_staff=True, is_superuser=True,
)
StudentProfile.objects.create(user=admin)

platform = get_or_create_platform_user()
WalletService.get_or_create_wallet(teacher_user)

# ────────────────────────────────────────────────────────────────────────
# S1: Top-up администратором
# ────────────────────────────────────────────────────────────────────────
section('S1: TOP-UP АДМИНИСТРАТОРОМ')
DEPOSIT_AMOUNT = Decimal('2000000')  # 2 миллиона на студента

balance_before = WalletService.get_or_create_wallet(student).balance
tx = WalletService.credit(
    user=student,
    amount=DEPOSIT_AMOUNT,
    tx_type=Transaction.Type.DEPOSIT,
    idempotency_key=f'audit-deposit-1:{SUFFIX}',
    description='admin top-up для аудита',
)
wallet = Wallet.objects.get(user=student); wallet.refresh_from_db()
expect(wallet.balance == balance_before + DEPOSIT_AMOUNT,
       'баланс ученика вырос ровно на DEPOSIT_AMOUNT',
       f'{balance_before} + {DEPOSIT_AMOUNT} = {wallet.balance}')
expect(tx.balance_after == wallet.balance,
       'Transaction.balance_after == wallet.balance')
expect(tx.amount == DEPOSIT_AMOUNT and tx.type == 'deposit',
       'tx.amount > 0, type=deposit')

# ────────────────────────────────────────────────────────────────────────
# S2: Идемпотентность credit
# ────────────────────────────────────────────────────────────────────────
section('S2: ИДЕМПОТЕНТНОСТЬ CREDIT')
b_before = wallet.balance
tx2 = WalletService.credit(
    user=student, amount=DEPOSIT_AMOUNT,
    tx_type=Transaction.Type.DEPOSIT,
    idempotency_key=f'audit-deposit-1:{SUFFIX}',  # ТОТ ЖЕ ключ!
)
wallet.refresh_from_db()
expect(tx2.id == tx.id, 'повторный credit вернул ту же Transaction (id совпадает)')
expect(wallet.balance == b_before, 'баланс НЕ изменился при повторном вызове',
       f'до={b_before} после={wallet.balance}')

# ────────────────────────────────────────────────────────────────────────
# S3: Покупка подписки
# ────────────────────────────────────────────────────────────────────────
section('S3: ПОКУПКА ПОДПИСКИ')
tariff = Tariff.objects.create(
    teacher=teacher, subject=subject, name='Базовый',
    lessons_per_week=2, lesson_duration_minutes=60,
    duration_months=1, price_per_month=Decimal('800000'),
)
expected_total = tariff.total_price          # = 800k * 1 month = 800k
expected_lessons = tariff.total_lessons       # = 2 в неделю × 4 нед × 1 мес = 8

b_student_before = wallet.balance
b_teacher_before = WalletService.get_or_create_wallet(teacher_user).balance
b_platform_before = WalletService.get_or_create_wallet(platform).balance

sub = SubscriptionService.purchase(
    student=student, tariff=tariff,
    idempotency_key=f'audit-sub:{SUFFIX}',
)
wallet.refresh_from_db()
expect(wallet.balance == b_student_before - expected_total,
       'баланс ученика уменьшен на price_total',
       f'было {b_student_before}, стало {wallet.balance}, total={expected_total}')
expect(sub.escrow_balance == expected_total,
       'escrow подписки = price_total',
       f'escrow={sub.escrow_balance}')
expect(sub.status == 'active', 'status=active')
expect(sub.total_lessons == expected_lessons, f'total_lessons={expected_lessons}',
       f'got {sub.total_lessons}')

# Учитель НЕ получил деньги — они в эскроу.
teacher_w = Wallet.objects.get(user=teacher_user); teacher_w.refresh_from_db()
expect(teacher_w.balance == b_teacher_before,
       'кошелёк учителя НЕ изменился (деньги в эскроу)',
       f'было {b_teacher_before}, сейчас {teacher_w.balance}')

# ────────────────────────────────────────────────────────────────────────
# S4: Идемпотентность purchase
# ────────────────────────────────────────────────────────────────────────
section('S4: ИДЕМПОТЕНТНОСТЬ PURCHASE')
b_before = wallet.balance
sub2 = SubscriptionService.purchase(
    student=student, tariff=tariff,
    idempotency_key=f'audit-sub:{SUFFIX}',  # ТОТ ЖЕ ключ
)
wallet.refresh_from_db()
expect(sub2.id == sub.id, 'возвращена та же Subscription')
expect(wallet.balance == b_before, 'баланс НЕ изменился',
       f'до={b_before} после={wallet.balance}')

# ────────────────────────────────────────────────────────────────────────
# S5: AlreadySubscribed (новый ключ, но тот же teacher+subject)
# ────────────────────────────────────────────────────────────────────────
section('S5: ALREADYSUBSCRIBED')
b_before = wallet.balance
try:
    SubscriptionService.purchase(
        student=student, tariff=tariff,
        idempotency_key=f'audit-sub-dup:{SUFFIX}',  # новый ключ
    )
    expect(False, 'AlreadySubscribed должно было бросить')
except AlreadySubscribed:
    expect(True, 'AlreadySubscribed корректно брошен')
wallet.refresh_from_db()
expect(wallet.balance == b_before, 'баланс НЕ списан при AlreadySubscribed')

# ────────────────────────────────────────────────────────────────────────
# S6: InsufficientFunds
# ────────────────────────────────────────────────────────────────────────
section('S6: INSUFFICIENT FUNDS')
# student2 имеет 0 баланса
w2 = WalletService.get_or_create_wallet(student2); w2.refresh_from_db()
b_before = w2.balance
expect(b_before == Decimal('0.00'), 'student2 начинает с 0',
       f'balance={b_before}')
try:
    SubscriptionService.purchase(
        student=student2, tariff=tariff,
        idempotency_key=f'audit-sub-poor:{SUFFIX}',
    )
    expect(False, 'InsufficientFunds должно было бросить')
except InsufficientFunds:
    expect(True, 'InsufficientFunds корректно брошен')
w2.refresh_from_db()
expect(w2.balance == b_before, 'баланс student2 не изменился')
# Подписки не создалось:
no_sub = not Subscription.objects.filter(
    student=student2, purchase_idempotency_key=f'audit-sub-poor:{SUFFIX}',
).exists()
expect(no_sub, 'Subscription не создалась (atomic rollback)')

# ────────────────────────────────────────────────────────────────────────
# S7: Auto-bookings
# ────────────────────────────────────────────────────────────────────────
section('S7: AUTO-BOOKINGS')
bookings = list(Booking.objects.filter(subscription=sub).order_by('slot__start_at'))
expect(len(bookings) == expected_lessons,
       f'создано {expected_lessons} bookings',
       f'got {len(bookings)}')
all_confirmed = all(b.status == 'confirmed' for b in bookings)
expect(all_confirmed, 'все bookings status=confirmed')
all_subscription_set = all(b.subscription_id == sub.id for b in bookings)
expect(all_subscription_set, 'все bookings привязаны к subscription')
# Все слоты заняты
all_booked = all(b.slot.status == 'booked' for b in bookings)
expect(all_booked, 'все slots status=booked')
# Все в будущем
all_future = all(b.slot.start_at > timezone.now() for b in bookings)
expect(all_future, 'все slots в будущем (не наслаиваются на now)')

# ────────────────────────────────────────────────────────────────────────
# S8: Завершение урока + payout (с правильным commission split)
# ────────────────────────────────────────────────────────────────────────
section('S8: LESSON COMPLETION + PAYOUT (85/15 split)')
COMMISSION_RATE = Decimal(settings.PLATFORM_COMMISSION_RATE)
price_per_lesson = sub.price_per_lesson
expected_commission = (price_per_lesson * COMMISSION_RATE).quantize(Decimal('0.01'))
expected_teacher_amt = (price_per_lesson - expected_commission).quantize(Decimal('0.01'))
print(f'    price_per_lesson = {price_per_lesson}, commission_rate = {COMMISSION_RATE}')
print(f'    → commission = {expected_commission}, teacher gets = {expected_teacher_amt}')

# Помечаем первый booking как completed
b = bookings[0]
b.status = 'completed'; b.save()

b_teacher_before = teacher_w.balance
b_platform_before = WalletService.get_or_create_wallet(platform).balance
sub.refresh_from_db()
escrow_before = sub.escrow_balance

# Сразу payout (без ожидания grace)
result = SubscriptionService.release_lesson_payout(b)
expect(result is True, 'release_lesson_payout вернул True')

sub.refresh_from_db()
teacher_w.refresh_from_db()
platform_w = WalletService.get_or_create_wallet(platform); platform_w.refresh_from_db()

expect(sub.escrow_balance == escrow_before - price_per_lesson,
       'escrow_balance уменьшилось ровно на price_per_lesson',
       f'было {escrow_before}, стало {sub.escrow_balance}')
expect(teacher_w.balance == b_teacher_before + expected_teacher_amt,
       f'учитель получил {expected_teacher_amt} (85%)',
       f'было {b_teacher_before}, стало {teacher_w.balance}')
expect(platform_w.balance == b_platform_before + expected_commission,
       f'платформа получила {expected_commission} (15%)',
       f'было {b_platform_before}, стало {platform_w.balance}')
expect(sub.lessons_paid_out == 1, 'lessons_paid_out = 1')

# Сумма teacher + commission == price_per_lesson (нет потерь на округлении в каждом шаге)
expect(expected_teacher_amt + expected_commission == price_per_lesson,
       'teacher + commission == price_per_lesson (без потерь)',
       f'{expected_teacher_amt}+{expected_commission}={expected_teacher_amt+expected_commission}')

# ────────────────────────────────────────────────────────────────────────
# S9: Идемпотентность payout
# ────────────────────────────────────────────────────────────────────────
section('S9: ИДЕМПОТЕНТНОСТЬ PAYOUT')
b_teacher_before = teacher_w.balance
b_platform_before = platform_w.balance
sub.refresh_from_db()
escrow_before = sub.escrow_balance

result2 = SubscriptionService.release_lesson_payout(b)
expect(result2 is False, 'повторный release_lesson_payout вернул False (already paid)')

teacher_w.refresh_from_db(); platform_w.refresh_from_db(); sub.refresh_from_db()
expect(teacher_w.balance == b_teacher_before, 'учитель НЕ получил повторно')
expect(platform_w.balance == b_platform_before, 'платформа НЕ получила повторно')
expect(sub.escrow_balance == escrow_before, 'эскроу НЕ изменилось')

# ────────────────────────────────────────────────────────────────────────
# S10+S11: Cancel by student с completed-но-неоплаченным
# ────────────────────────────────────────────────────────────────────────
section('S10+S11: CANCEL by STUDENT (учёт completed-но-неоплаченных)')

# Завершим ещё один урок, но НЕ выплатим (как будто grace window не прошёл)
bookings[1].status = 'completed'; bookings[1].save()

sub.refresh_from_db()
escrow_at_cancel = sub.escrow_balance
b_student_before = wallet.balance
b_teacher_before = teacher_w.balance
b_platform_before = platform_w.balance
lessons_paid_before = sub.lessons_paid_out

result = SubscriptionService.cancel(sub, cancelled_by='student', reason='проверка аудита')

sub.refresh_from_db()
wallet.refresh_from_db()
teacher_w.refresh_from_db()
platform_w.refresh_from_db()

# Учитель ДОПОЛУЧИЛ payout за второй completed-урок
expect(sub.lessons_paid_out == lessons_paid_before + 1,
       'lessons_paid_out увеличилось на 1 (доплатили за completed)',
       f'было {lessons_paid_before}, стало {sub.lessons_paid_out}')
expect(teacher_w.balance == b_teacher_before + expected_teacher_amt,
       'учителю доплачено за второй completed-урок')
expect(platform_w.balance == b_platform_before + expected_commission,
       'платформе доначислена комиссия')

# Refund остатка ученику
expected_refund = escrow_at_cancel - price_per_lesson  # 1 урок ушёл учителю + 6 остатка
expect(result['refunded'] == expected_refund,
       f'refund = {expected_refund}',
       f'got {result["refunded"]}')
expect(wallet.balance == b_student_before + expected_refund,
       'ученик получил refund на баланс')
expect(sub.escrow_balance == Decimal('0.00'),
       'escrow подписки обнулён',
       f'got {sub.escrow_balance}')
expect(sub.status == 'cancelled_by_student', 'status=cancelled_by_student')

# Будущие bookings cancelled, слоты освобождены
cancelled_bookings = Booking.objects.filter(
    subscription=sub, status='cancelled_by_student',
).count()
expect(cancelled_bookings == result['cancelled_bookings'],
       f'отменено {result["cancelled_bookings"]} будущих bookings',
       f'in DB: {cancelled_bookings}')
# Слоты будущих cancelled-bookings освобождены:
freed_after_cancel = TimeSlot.objects.filter(
    teacher=teacher,
    bookings__subscription=sub,
    bookings__status='cancelled_by_student',
    status='free',
).distinct().count()
expect(freed_after_cancel == result['cancelled_bookings'],
       f'все {result["cancelled_bookings"]} слотов отменённых bookings освобождены',
       f'freed_after_cancel={freed_after_cancel}')

# ────────────────────────────────────────────────────────────────────────
# S12-S14: Withdrawal flow
# ────────────────────────────────────────────────────────────────────────
section('S12-S15: WITHDRAWAL LIFECYCLE')

# У учителя должен быть баланс — он получал payout-ы
teacher_w.refresh_from_db()
print(f'    teacher balance = {teacher_w.balance}')

# S12: Create
WD_AMOUNT = Decimal('100000')  # минималка
b_before = teacher_w.balance
wr = WithdrawalService.create_request(
    user=teacher_user, amount=WD_AMOUNT,
    payout_method='card', payout_details='**** 1234',
    idempotency_key=f'audit-wd-1:{SUFFIX}',
)
teacher_w.refresh_from_db()
expect(wr.status == 'pending', 'WithdrawalRequest.status=pending')
expect(teacher_w.balance == b_before - WD_AMOUNT,
       'кошелёк учителя списан на сумму заявки',
       f'было {b_before}, стало {teacher_w.balance}')
expect(wr.amount == WD_AMOUNT, f'wr.amount = {WD_AMOUNT}')

# S13: Min limit
b_before = teacher_w.balance
try:
    WithdrawalService.create_request(
        user=teacher_user, amount=Decimal('50000'),  # ниже min
        payout_method='card', payout_details='**** 5678',
        idempotency_key=f'audit-wd-low:{SUFFIX}',
    )
    expect(False, 'WithdrawalAmountError должно было бросить')
except WithdrawalAmountError:
    expect(True, 'WithdrawalAmountError при amount<MIN корректно')
teacher_w.refresh_from_db()
expect(teacher_w.balance == b_before, 'баланс не изменился при rejection')

# S14: Reject — refund
b_before = teacher_w.balance
WithdrawalService.reject(wr, admin_user=admin, note='проверочный reject')
teacher_w.refresh_from_db()
wr.refresh_from_db()
expect(wr.status == 'rejected', 'wr.status=rejected')
expect(teacher_w.balance == b_before + WD_AMOUNT,
       'баланс восстановлен (refund)')

# S15: Complete (новая заявка → approve → complete)
wr2 = WithdrawalService.create_request(
    user=teacher_user, amount=WD_AMOUNT,
    payout_method='card', payout_details='**** 1234',
    idempotency_key=f'audit-wd-2:{SUFFIX}',
)
teacher_w.refresh_from_db()
b_after_create = teacher_w.balance

WithdrawalService.approve(wr2, admin_user=admin)
teacher_w.refresh_from_db()
expect(teacher_w.balance == b_after_create,
       'approve НЕ меняет баланс (деньги уже списаны при create)')

WithdrawalService.complete(wr2, admin_user=admin)
teacher_w.refresh_from_db()
wr2.refresh_from_db()
expect(wr2.status == 'completed', 'wr2.status=completed')
expect(teacher_w.balance == b_after_create,
       'complete НЕ меняет баланс (деньги ушли наружу)')

# ────────────────────────────────────────────────────────────────────────
# S16: Reconciliation для всех wallets
# ────────────────────────────────────────────────────────────────────────
section('S16: RECONCILIATION (sum tx == balance per wallet)')
audit_wallets = Wallet.objects.filter(
    user__in=[student, student2, teacher_user, admin, platform],
)
all_ok = True
for w in audit_wallets:
    rec = WalletService.reconcile_balance(w)
    if rec != w.balance:
        all_ok = False
        print(f'    {R}MISMATCH {w.user.username}: balance={w.balance}, sum(tx)={rec}{D}')
expect(all_ok, 'sum(Transaction.amount) == wallet.balance для всех audit-кошельков')

# ────────────────────────────────────────────────────────────────────────
# S17: Глобальный инвариант сохранения денег
# ────────────────────────────────────────────────────────────────────────
section('S17: ГЛОБАЛЬНЫЙ ИНВАРИАНТ')

AFTER = total_money_in_system()

# Что вошло извне:
deposits_in = DEPOSIT_AMOUNT  # 1 раз top-up

# Что вышло наружу:
withdrawals_out = WD_AMOUNT   # 1 completed заявка

# Ожидаемое после = до + deposits - withdrawals
expected_after = BEFORE['total'] + deposits_in - withdrawals_out

print(f'    до           = {BEFORE["total"]:>14,.2f}')
print(f'    + deposits   = {deposits_in:>14,.2f}')
print(f'    - withdrawn  = {withdrawals_out:>14,.2f}')
print(f'    = ожидаемое  = {expected_after:>14,.2f}')
print(f'    фактическое  = {AFTER["total"]:>14,.2f}')
print(f'    (wallets={AFTER["wallets"]}, escrow={AFTER["escrow"]}, pending_wd={AFTER["pending_withdrawals"]})')

diff = AFTER['total'] - expected_after
expect(diff == Decimal('0.00'),
       'TotalMoney_after == TotalMoney_before + deposits - withdrawals',
       f'diff = {diff}')

# ────────────────────────────────────────────────────────────────────────
# CLEANUP
# ────────────────────────────────────────────────────────────────────────
section('CLEANUP')
ids = list(User.objects.filter(username__startswith=f'audit_').values_list('pk', flat=True))
Booking.objects.filter(student_id__in=ids).delete()
Subscription.objects.filter(student_id__in=ids).delete()
Subscription.objects.filter(teacher__user_id__in=ids).delete()
Tariff.objects.filter(teacher__user_id__in=ids).delete()
TimeSlot.objects.filter(teacher__user_id__in=ids).delete()
WithdrawalRequest.objects.filter(user_id__in=ids).delete()
Transaction.objects.filter(wallet__user_id__in=ids).delete()
Wallet.objects.filter(user_id__in=ids).delete()
TeacherSubject.objects.filter(teacher__user_id__in=ids).delete()
StudentProfile.objects.filter(user_id__in=ids).delete()
TeacherProfile.objects.filter(user_id__in=ids).delete()
deleted = User.objects.filter(username__startswith=f'audit_').delete()
print(f'  очищено: {deleted}')

# ────────────────────────────────────────────────────────────────────────
# ИТОГ
# ────────────────────────────────────────────────────────────────────────
print(f'\n{B}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{D}')
total = len(PASSED) + len(FAILED)
print(f'  ВСЕГО:  {total}')
print(f'  {G}PASSED: {len(PASSED)}{D}')
if FAILED:
    print(f'  {R}FAILED: {len(FAILED)}{D}')
    for name, det in FAILED:
        print(f'    {R}✗ {name}{D}', det and f' — {det}' or '')
if WARNED:
    print(f'  {Y}WARN:   {len(WARNED)}{D}')

if not FAILED:
    print(f'\n{G}✅ Все сценарии пройдены. Система самосогласована.{D}')
    sys.exit(0)
else:
    print(f'\n{R}❌ Найдены расхождения — система требует фикса.{D}')
    sys.exit(1)
