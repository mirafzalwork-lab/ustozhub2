"""Smoke-тест Phase 6 — withdrawal flow.

Сценарий:
  1. У учителя 500 000 на балансе.
  2. Через UI создаёт заявку на 200 000 → wallet=300k, status=pending.
  3. Двойной submit (тот же idempotency) → одна заявка.
  4. Отменяет → wallet=500k, status=cancelled.
  5. Создаёт новую 300 000 → wallet=200k.
  6. Админ approve → status=approved.
  7. Админ complete → status=completed. Wallet 200k (не возвращается).
  8. Проверка UI: список заявок, кнопка вывода в профиле.
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
from billing.models import Subscription, Tariff, Transaction, Wallet, WithdrawalRequest
from billing.services import WalletService, WithdrawalService

User = get_user_model()

def green(s): return f'\033[32m{s}\033[0m'
def red(s):   return f'\033[31m{s}\033[0m'

SUFFIX = uuid.uuid4().hex[:6]
T_USER = f'p6_teacher_{SUFFIX}'
A_USER = f'p6_admin_{SUFFIX}'

old = User.objects.filter(username__startswith='p6_')
old_ids = list(old.values_list('pk', flat=True))
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
    old.delete()

teacher_user = User.objects.create_user(
    username=T_USER, email=f'{T_USER}@x.com', password='Password123', user_type='teacher',
)
TeacherProfile.objects.create(user=teacher_user, experience_years=3, moderation_status='approved')
WalletService.credit(
    user=teacher_user, amount=Decimal('500000'),
    tx_type=Transaction.Type.DEPOSIT, idempotency_key=f'p6-seed-{SUFFIX}',
)

admin_user = User.objects.create_user(
    username=A_USER, email=f'{A_USER}@x.com', password='Password123',
    is_staff=True, is_superuser=True,
)

# UI client
tc = Client()
tc.login(username=T_USER, password='Password123')

# Кнопка в профиле
r = tc.get('/ru/profile/')
shows_btn = 'Вывести средства' in r.content.decode('utf-8')
print(f'[1] Кнопка "Вывести средства" в профиле:', green('есть') if shows_btn else red('нет'))

# Страница withdrawals
r = tc.get('/ru/profile/withdrawals/')
print(f'[2] GET withdrawals_list: {r.status_code}', green('OK') if r.status_code == 200 else red('FAIL'))
shows_balance = '500 000' in r.content.decode('utf-8').replace('\xa0', ' ')
print(f'   → видит баланс 500 000:', green('да') if shows_balance else red('нет'))

# POST: создание заявки
idem1 = str(uuid.uuid4())
r = tc.post('/ru/profile/withdrawals/', {
    'amount': '200000',
    'payout_method': 'card',
    'payout_details': '8600 1234 5678 9012',
    'comment': 'на карту Humo',
    'idempotency_key': idem1,
})
print(f'[3] POST создать заявку: {r.status_code} (ожидаем 302)',
      green('OK') if r.status_code == 302 else red('FAIL'))

wr = WithdrawalRequest.objects.filter(user=teacher_user).first()
teacher_user.wallet.refresh_from_db()
print(f'[4] wallet={teacher_user.wallet.balance} (ожидаем 300000), wr.status={wr.status if wr else None}',
      green('OK') if (teacher_user.wallet.balance == Decimal('300000') and wr and wr.status == 'pending') else red('FAIL'))

# Идемпотентность: повтор того же ключа
r = tc.post('/ru/profile/withdrawals/', {
    'amount': '200000',
    'payout_method': 'card',
    'payout_details': '8600 1234 5678 9012',
    'idempotency_key': idem1,
})
count = WithdrawalRequest.objects.filter(user=teacher_user).count()
print(f'[5] идемпотентность: заявок={count} (ожидаем 1)',
      green('OK') if count == 1 else red('FAIL'))

# Отмена пользователем
r = tc.post(f'/ru/profile/withdrawals/{wr.id}/cancel/')
wr.refresh_from_db()
teacher_user.wallet.refresh_from_db()
print(f'[6] POST cancel: wr.status={wr.status}, wallet={teacher_user.wallet.balance}',
      green('OK') if wr.status == 'cancelled' and teacher_user.wallet.balance == Decimal('500000') else red('FAIL'))

# Создание новой заявки 300k
idem2 = str(uuid.uuid4())
r = tc.post('/ru/profile/withdrawals/', {
    'amount': '300000',
    'payout_method': 'phone',
    'payout_details': '+998901234567',
    'idempotency_key': idem2,
})
wr2 = WithdrawalRequest.objects.filter(user=teacher_user, status='pending').first()
print(f'[7] вторая заявка: wr.amount={wr2.amount if wr2 else None}',
      green('OK') if wr2 and wr2.amount == Decimal('300000') else red('FAIL'))

# Approve через сервис (имитация админ-action)
WithdrawalService.approve(wr2, admin_user=admin_user, note='проверено')
wr2.refresh_from_db()
print(f'[8] approve: status={wr2.status}',
      green('OK') if wr2.status == 'approved' else red('FAIL'))

# Complete
WithdrawalService.complete(wr2, admin_user=admin_user, note='перевод выполнен 25.05.2026')
wr2.refresh_from_db()
teacher_user.wallet.refresh_from_db()
print(f'[9] complete: status={wr2.status}, wallet={teacher_user.wallet.balance} (ожидаем 200000)',
      green('OK') if wr2.status == 'completed' and teacher_user.wallet.balance == Decimal('200000') else red('FAIL'))

# UI: история показывает обе заявки + статусы
r = tc.get('/ru/profile/withdrawals/')
html = r.content.decode('utf-8')
print(f'[10] UI history: pending заявок (cancelled+completed):',
      green('обе видны') if 'cancelled' in html and 'completed' in html else red('что-то не видно'))

# Min-amount validation на форме
r = tc.post('/ru/profile/withdrawals/', {
    'amount': '50000',
    'payout_method': 'card',
    'payout_details': 'c',
    'idempotency_key': str(uuid.uuid4()),
})
# Form invalid → возврат 200 с ошибкой, не 302
print(f'[11] min-amount валидация: HTTP {r.status_code} (ожидаем 200 c ошибкой)',
      green('OK') if r.status_code == 200 else red('FAIL'))

# Final reconcile: проверим что сумма всех Transaction в кошельке = balance
from billing.services import WalletService as WS
reconciled = WS.reconcile_balance(teacher_user.wallet)
print(f'[12] reconcile: balance={teacher_user.wallet.balance} == ledger sum={reconciled}',
      green('OK') if reconciled == teacher_user.wallet.balance else red('FAIL'))

print()
print(green('✅ P6 smoke завершён.'))
