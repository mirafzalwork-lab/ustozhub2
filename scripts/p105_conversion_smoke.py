"""Smoke-тест Phase 10.5 — Конверсия пробного → тариф.

Сценарии:
  Setup: учитель + ученик + тариф + completed trial
  S1.  Дашборд ученика показывает банер «Вы прошли пробный»
  S2.  В банере есть тариф учителя
  S3.  Страница учителя — тариф подсвечен «Рекомендуется после пробного»
  S4.  После покупки подписки — банер ИСЧЕЗАЕТ (ученик уже подписан)
  S5.  Без пробного — банер НЕ показывается
  S6.  Пробный старше 30 дней — банер НЕ показывается
"""
import os, sys, django, uuid
from datetime import timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from teachers.models import (
    Booking, Subject, SubjectCategory, StudentProfile, TeacherProfile,
    TeacherSubject, TimeSlot,
)
from billing.models import Subscription, Tariff, Transaction, Wallet
from billing.services import SubscriptionService, WalletService

User = get_user_model()
G='\033[32m'; R='\033[31m'; B='\033[34m'; D='\033[0m'

P=0; F=0
def check(c, name, det=''):
    global P, F
    if c: P+=1; print(f'  {G}✓{D} {name}', f'— {det}' if det else '')
    else: F+=1; print(f'  {R}✗ {name}{D}', f'— {det}' if det else '')

SUFFIX = uuid.uuid4().hex[:6]
T = f'p105_t_{SUFFIX}'
S = f'p105_s_{SUFFIX}'
S2 = f'p105_s2_{SUFFIX}'
S3 = f'p105_s3_{SUFFIX}'

print(f'\n{B}━━━━━━ PHASE 10.5: CONVERSION FUNNEL SMOKE — {SUFFIX} ━━━━━━{D}')

# Cleanup
old = User.objects.filter(username__startswith='p105_')
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
teacher_user = User.objects.create_user(
    username=T, email=f'{T}@x.com', password='Pass123', user_type='teacher',
)
teacher = TeacherProfile.objects.create(
    user=teacher_user, experience_years=3,
    moderation_status='approved', is_active=True,
    weekly_schedule={d: [{'from':'09:00','to':'13:00'}] for d in
                     ('monday','tuesday','wednesday','thursday','friday')},
)
cat, _ = SubjectCategory.objects.get_or_create(name='Языки')
subject, _ = Subject.objects.get_or_create(name='Английский', defaults={'category': cat})
TeacherSubject.objects.create(teacher=teacher, subject=subject, hourly_rate=Decimal('80000'))

tariff = Tariff.objects.create(
    teacher=teacher, subject=subject, name='Базовый месяц',
    lessons_per_week=2, lesson_duration_minutes=60,
    duration_months=1, price_per_month=Decimal('600000'),
)

# Ученик 1 — пройдёт пробный
student = User.objects.create_user(
    username=S, email=f'{S}@x.com', password='Pass123',
    user_type='student', first_name='Аня',
)
StudentProfile.objects.create(user=student)

# Ученик 2 — без пробного (для негативного теста)
student2 = User.objects.create_user(
    username=S2, email=f'{S2}@x.com', password='Pass123',
    user_type='student', first_name='Боря',
)
StudentProfile.objects.create(user=student2)

# Ученик 3 — пробный старше 30 дней
student3 = User.objects.create_user(
    username=S3, email=f'{S3}@x.com', password='Pass123',
    user_type='student', first_name='Вика',
)
StudentProfile.objects.create(user=student3)

# Топ-ап
for u in (student, student2, student3):
    WalletService.credit(
        user=u, amount=Decimal('1000000'),
        tx_type=Transaction.Type.DEPOSIT,
        idempotency_key=f'p105-seed-{u.username}-{SUFFIX}',
    )

now = timezone.now()

# Пробный завершён 2ч назад — Аня
slot_a = TimeSlot.objects.create(
    teacher=teacher,
    start_at=now - timedelta(hours=3),
    end_at=now - timedelta(hours=2),
    status='booked',
)
trial_a = Booking.objects.create(
    slot=slot_a, student=student, subject=subject,
    is_trial=True, status='completed',
)

# Пробный завершён 45 дней назад — Вика
slot_v = TimeSlot.objects.create(
    teacher=teacher,
    start_at=now - timedelta(days=45, hours=1),
    end_at=now - timedelta(days=45),
    status='booked',
)
trial_v = Booking.objects.create(
    slot=slot_v, student=student3, subject=subject,
    is_trial=True, status='completed',
)


# ─────────── S1+S2: банер у Ани
print(f'\n{B}━━━ S1+S2: BANNER SHOWN (Аня после пробного) ━━━{D}')
ca = Client(); ca.login(username=S, password='Pass123')
r = ca.get('/ru/dashboard/')
check(r.status_code == 200, 'GET /dashboard/ → 200')
html = r.content.decode('utf-8')
check('Вы прошли пробный' in html, 'банер «Вы прошли пробный» виден')
check('Готовы продолжить' in html, 'CTA «Готовы продолжить?» виден')
check('Базовый месяц' in html, 'тариф «Базовый месяц» виден в банере')
check('600000' in html, 'цена тарифа отображается')


# ─────────── S3: подсветка тарифа на странице учителя
print(f'\n{B}━━━ S3: TARIFF HIGHLIGHTED ON TEACHER PAGE ━━━{D}')
r = ca.get(f'/ru/teacher/{teacher.id}/')
html = r.content.decode('utf-8')
check(r.status_code == 200, 'GET /teacher/<id>/ → 200')
check('Рекомендуется после пробного' in html,
      'тариф подсвечен «Рекомендуется после пробного»')


# ─────────── S5: ученик БЕЗ пробного (Боря)
print(f'\n{B}━━━ S5: BORYA (no trial) — NO BANNER ━━━{D}')
cb = Client(); cb.login(username=S2, password='Pass123')
r = cb.get('/ru/dashboard/')
html = r.content.decode('utf-8')
check('Вы прошли пробный' not in html,
      'банер НЕ показывается ученику без пробного')


# ─────────── S6: пробный старше 30 дней (Вика)
print(f'\n{B}━━━ S6: STALE TRIAL (>30 days) — NO BANNER ━━━{D}')
cv = Client(); cv.login(username=S3, password='Pass123')
r = cv.get('/ru/dashboard/')
html = r.content.decode('utf-8')
check('Вы прошли пробный' not in html,
      'банер НЕ показывается, если пробный старше 30 дней')


# ─────────── S4: после покупки подписки банер исчезает
print(f'\n{B}━━━ S4: AFTER SUBSCRIBE — BANNER GONE ━━━{D}')
sub = SubscriptionService.purchase(
    student=student, tariff=tariff,
    idempotency_key=f'p105-buy-{SUFFIX}',
)
r = ca.get('/ru/dashboard/')
html = r.content.decode('utf-8')
check('Вы прошли пробный' not in html,
      'банер исчез после подписки на тариф')

# Также на странице учителя подсветка должна уйти
r = ca.get(f'/ru/teacher/{teacher.id}/')
html = r.content.decode('utf-8')
check('Рекомендуется после пробного' not in html,
      'подсветка тарифа исчезла после подписки')


# ─────────── CLEANUP
print(f'\n{B}━━━ CLEANUP ━━━{D}')
ids = list(User.objects.filter(username__startswith='p105_').values_list('pk', flat=True))
Booking.objects.filter(student_id__in=ids).delete()
Subscription.objects.filter(student_id__in=ids).delete()
Subscription.objects.filter(teacher__user_id__in=ids).delete()
Tariff.objects.filter(teacher__user_id__in=ids).delete()
TimeSlot.objects.filter(teacher__user_id__in=ids).delete()
Transaction.objects.filter(wallet__user_id__in=ids).delete()
Wallet.objects.filter(user_id__in=ids).delete()
TeacherSubject.objects.filter(teacher__user_id__in=ids).delete()
StudentProfile.objects.filter(user_id__in=ids).delete()
TeacherProfile.objects.filter(user_id__in=ids).delete()
deleted = User.objects.filter(username__startswith='p105_').delete()
print(f'  очищено: {deleted}')

print(f'\n{B}━━━━━━━━━━━━━━━━━━━━━━━━{D}')
print(f'  ВСЕГО:  {P+F}')
print(f'  {G}PASSED:{D} {P}')
if F:
    print(f'  {R}FAILED:{D} {F}')
    sys.exit(1)
print(f'\n{G}✅ P10.5 conversion funnel smoke завершён.{D}')
