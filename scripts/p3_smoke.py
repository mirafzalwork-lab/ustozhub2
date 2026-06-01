"""Smoke-тест Phase 3 — UI/UX в реальном Django, без DB-mock.

Запуск:  python scripts/p3_smoke.py

Создаёт:
  * учителя с weekly_schedule + тариф
  * ученика с балансом
  * через test client проверяет:
    - GET страница тарифов (учитель)
    - GET страница teacher_detail (видит тарифы)
    - GET /subscriptions/buy/<id>/
    - POST покупка → создаёт Subscription + 8 bookings
    - GET /my/subscriptions/
    - GET /profile/subscribers/ (как учитель)

Пишет PASS/FAIL по каждому шагу.
"""
import os, sys, django
from decimal import Decimal
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.test import Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from teachers.models import (
    Booking, Subject, SubjectCategory, StudentProfile, TeacherProfile,
    TeacherSubject, TimeSlot,
)
from billing.models import Subscription, Tariff, Transaction, Wallet
from billing.services import WalletService

User = get_user_model()

def green(s): return f'\033[32m{s}\033[0m'
def red(s):   return f'\033[31m{s}\033[0m'

# ---------- setup ----------
SUFFIX = uuid.uuid4().hex[:6]
T_USER = f'p3_teacher_{SUFFIX}'
S_USER = f'p3_student_{SUFFIX}'

# Очистка предыдущих прогонов — снимаем PROTECT, удаляя зависимые сущности первыми.
old_users = User.objects.filter(username__startswith='p3_')
old_user_ids = list(old_users.values_list('pk', flat=True))
if old_user_ids:
    Booking.objects.filter(student_id__in=old_user_ids).delete()
    Subscription.objects.filter(student_id__in=old_user_ids).delete()
    Subscription.objects.filter(teacher__user_id__in=old_user_ids).delete()
    Tariff.objects.filter(teacher__user_id__in=old_user_ids).delete()
    TimeSlot.objects.filter(teacher__user_id__in=old_user_ids).delete()
    Transaction.objects.filter(wallet__user_id__in=old_user_ids).delete()
    Wallet.objects.filter(user_id__in=old_user_ids).delete()
    TeacherSubject.objects.filter(teacher__user_id__in=old_user_ids).delete()
    StudentProfile.objects.filter(user_id__in=old_user_ids).delete()
    TeacherProfile.objects.filter(user_id__in=old_user_ids).delete()
    old_users.delete()

print(f'→ Создаём учителя {T_USER}')
teacher_user = User.objects.create_user(
    username=T_USER, email=f'{T_USER}@x.com', password='Password123',
    user_type='teacher',
)
teacher = TeacherProfile.objects.create(
    user=teacher_user, experience_years=5,
    moderation_status='approved',
    is_active=True,
    weekly_schedule={
        'monday':    [{'from': '09:00', 'to': '13:00'}],
        'tuesday':   [{'from': '09:00', 'to': '13:00'}],
        'wednesday': [{'from': '09:00', 'to': '13:00'}],
        'thursday':  [{'from': '09:00', 'to': '13:00'}],
        'friday':    [{'from': '09:00', 'to': '13:00'}],
    },
)
cat, _ = SubjectCategory.objects.get_or_create(name='Языки')
subject, _ = Subject.objects.get_or_create(name='Английский', defaults={'category': cat})
TeacherSubject.objects.create(teacher=teacher, subject=subject, hourly_rate=Decimal('80000'))

print(f'→ Создаём ученика {S_USER}')
student = User.objects.create_user(
    username=S_USER, email=f'{S_USER}@x.com', password='Password123',
    user_type='student',
)
# StudentProfile нужен, иначе OnboardingMiddleware редиректит на /register/choose/.
StudentProfile.objects.create(user=student)
WalletService.credit(
    user=student, amount=Decimal('2000000'),
    tx_type=Transaction.Type.DEPOSIT,
    idempotency_key=f'smoke-seed-{SUFFIX}',
    description='smoke seed',
)
student.wallet.refresh_from_db()
print(f'   → ученик имеет баланс: {student.wallet.balance}')

# ---------- 1. учитель создаёт тариф через UI ----------
tc = Client()
tc.login(username=T_USER, password='Password123')

r = tc.get('/ru/profile/tariffs/')
print(f'[1] GET тарифы (учитель): {r.status_code}', green('OK') if r.status_code == 200 else red('FAIL'))

r = tc.post('/ru/profile/tariffs/new/', {
    'subject': subject.id,
    'name': 'Базовый',
    'description': 'Регулярные занятия английским',
    'lessons_per_week': 2,
    'lesson_duration_minutes': 60,
    'duration_months': 1,
    'price_per_month': '800000',
    'is_active': 'on',
})
print(f'[2] POST создать тариф: {r.status_code} (ожидаем 302)',
      green('OK') if r.status_code == 302 else red('FAIL'))

tariff = Tariff.objects.get(teacher=teacher)
print(f'   → tariff: {tariff.name} {tariff.total_price} сум / {tariff.total_lessons} уроков / {tariff.price_per_lesson} за урок')
print(f'   → tariff.is_active={tariff.is_active}, teacher.tariffs.filter(is_active=True).count()={teacher.tariffs.filter(is_active=True).count()}')

# ---------- 2. публичная страница учителя видна и показывает тариф ----------
sc = Client()
r = sc.get(f'/ru/teacher/{teacher.id}/')
print(f'[3] GET публичная teacher_detail: {r.status_code}',
      green('OK') if r.status_code == 200 else red('FAIL'))
shows_tariff = 'Подписки и тарифы' in r.content.decode('utf-8')
shows_login_cta = 'Войти, чтобы купить' in r.content.decode('utf-8')
print(f'   → секция "Подписки и тарифы":', green('видна') if shows_tariff else red('НЕТ'))
print(f'   → CTA для анона "Войти, чтобы купить":', green('есть') if shows_login_cta else red('НЕТ'))

# Та же страница, но залогиненным учеником — должна быть кнопка «Купить»
sc2 = Client()
sc2.login(username=S_USER, password='Password123')
r2 = sc2.get(f'/ru/teacher/{teacher.id}/')
shows_buy = f'/subscriptions/buy/{tariff.id}/' in r2.content.decode('utf-8')
print(f'   → CTA "Купить" для залогиненного ученика:', green('есть') if shows_buy else red('НЕТ'))

# ---------- 3. ученик заходит на checkout ----------
sc.login(username=S_USER, password='Password123')
r = sc.get(f'/ru/subscriptions/buy/{tariff.id}/')
print(f'[4] GET checkout-страница: {r.status_code}',
      green('OK') if r.status_code == 200 else red('FAIL'))

# ---------- 4. ученик покупает ----------
import re
m = re.search(rb'name="idempotency_key" value="([^"]+)"', r.content)
idem = m.group(1).decode() if m else 'fallback-' + SUFFIX

r = sc.post(f'/ru/subscriptions/buy/{tariff.id}/', {'idempotency_key': idem})
print(f'[5] POST купить: {r.status_code} (ожидаем 302)',
      green('OK') if r.status_code == 302 else red('FAIL'))

sub = Subscription.objects.filter(student=student).first()
if sub:
    print(f'   → subscription: status={sub.status} escrow={sub.escrow_balance}')
    bookings = Booking.objects.filter(subscription=sub)
    print(f'   → bookings создано: {bookings.count()} / {sub.total_lessons}',
          green('OK') if bookings.count() == sub.total_lessons else red('FAIL'))
    fresh_wallet = Wallet.objects.get(user=student)
    expected_balance = Decimal('2000000') - sub.price_total
    print(f'   → student.balance: {fresh_wallet.balance} (ожидаем {expected_balance})',
          green('OK') if fresh_wallet.balance == expected_balance else red('FAIL'))
else:
    print(red('   ✗ Subscription не создана!'))

# ---------- 5. /my/subscriptions/ ----------
r = sc.get('/ru/my/subscriptions/')
print(f'[6] GET my_subscriptions (ученик): {r.status_code}',
      green('OK') if r.status_code == 200 else red('FAIL'))

# ---------- 6. учитель видит подписчиков ----------
r = tc.get('/ru/profile/subscribers/')
print(f'[7] GET teacher_subscribers (учитель): {r.status_code}',
      green('OK') if r.status_code == 200 else red('FAIL'))
shows_student = bytes(student.username, 'utf-8') in r.content
print(f'   → ученик виден в списке:', green('да') if shows_student else red('нет'))

# ---------- 7. идемпотентность покупки ----------
r = sc.post(f'/ru/subscriptions/buy/{tariff.id}/', {'idempotency_key': idem})
count = Subscription.objects.filter(student=student).count()
print(f'[8] идемпотентность (повтор того же idem): подписок={count}',
      green('OK (1)') if count == 1 else red(f'FAIL ({count})'))

print()
print(green('✅ Smoke завершён.'))
