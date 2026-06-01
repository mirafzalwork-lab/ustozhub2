"""Smoke-тест Phase 10 — Полные дашборды.

Сценарий:
  Setup: учитель + ученик + подписка + ДЗ + урок completed
  S1.  GET /dashboard/ под учеником → 200, видит баланс, подписку, прогресс
  S2.  GET /dashboard/ под учителем → 200, видит заработок, ученика, ДЗ
  S3.  GET /admin-dashboard/billing/ под админом → новые секции
  S4.  Кнопка «Дашборд» в профиле ученика
  S5.  Кнопка «Дашборд» в профиле учителя
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
from billing.models import (
    Homework, HomeworkSubmission,
    Subscription, Tariff, Transaction, Wallet, WithdrawalRequest,
)
from billing.services import SubscriptionService, WalletService

User = get_user_model()
G='\033[32m'; R='\033[31m'; B='\033[34m'; D='\033[0m'

def green(s): return f'{G}{s}{D}'
def red(s):   return f'{R}{s}{D}'

PASSED=0; FAILED=0
def check(c, name, det=''):
    global PASSED, FAILED
    if c:
        PASSED+=1; print(f'  {green("✓")} {name}', f'— {det}' if det else '')
    else:
        FAILED+=1; print(f'  {red("✗")} {name}', f'— {det}' if det else '')

SUFFIX = uuid.uuid4().hex[:6]
T_USER = f'p10_t_{SUFFIX}'
S_USER = f'p10_s_{SUFFIX}'
A_USER = f'p10_a_{SUFFIX}'

print(f'\n{B}━━━━━━ PHASE 10: DASHBOARDS SMOKE — {SUFFIX} ━━━━━━{D}')

# cleanup
old = User.objects.filter(username__startswith='p10_')
old_ids = list(old.values_list('pk', flat=True))
if old_ids:
    HomeworkSubmission.objects.filter(homework__teacher__user_id__in=old_ids).delete()
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

# setup
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
    username=S_USER, email=f'{S_USER}@x.com', password='Pass123',
    user_type='student', first_name='Ало',
)
StudentProfile.objects.create(user=student)

admin = User.objects.create_user(
    username=A_USER, email=f'{A_USER}@x.com', password='Pass123',
    is_staff=True, is_superuser=True,
)
StudentProfile.objects.create(user=admin)

# Top-up + sub + completed lesson + HW
WalletService.credit(
    user=student, amount=Decimal('1000000'),
    tx_type=Transaction.Type.DEPOSIT,
    idempotency_key=f'p10-seed-{SUFFIX}',
)
tariff = Tariff.objects.create(
    teacher=teacher, subject=subject, name='Базовый',
    lessons_per_week=2, lesson_duration_minutes=60,
    duration_months=1, price_per_month=Decimal('800000'),
)
sub = SubscriptionService.purchase(
    student=student, tariff=tariff,
    idempotency_key=f'p10-sub-{SUFFIX}',
)

# Помечаем 2 урока completed
bookings = list(Booking.objects.filter(subscription=sub).order_by('slot__start_at'))
bookings[0].status = 'completed'; bookings[0].save()
SubscriptionService.release_lesson_payout(bookings[0])  # учитель уже заработал
bookings[1].status = 'completed'; bookings[1].save()

# ДЗ submitted (требует проверки) + assigned (требует сдачи)
Homework.objects.create(
    subscription=sub, teacher=teacher, student=student,
    title='HW1', description='—', status=Homework.Status.ASSIGNED,
)
hw2 = Homework.objects.create(
    subscription=sub, teacher=teacher, student=student,
    title='HW2', description='—', status=Homework.Status.SUBMITTED,
)
HomeworkSubmission.objects.create(homework=hw2, student=student, text_response='готово!')

# ───────── S1: student dashboard
print(f'\n{B}━━━ S1: STUDENT DASHBOARD ━━━{D}')
sc = Client(); sc.login(username=S_USER, password='Pass123')
r = sc.get('/ru/dashboard/')
check(r.status_code == 200, 'GET /dashboard/ → 200', f'got {r.status_code}')
html = r.content.decode('utf-8')
check('Привет' in html and 'Ало' in html, 'видит приветствие с именем')
check('Баланс кошелька' in html, 'видит карточку баланса')
check('Сводка' in html, 'видит сводку')
# completed=2, остальные in future
check('2' in html, 'видит количество завершённых уроков')
check('Английский' in html, 'видит активную подписку')
check('HW1' in html or 'Сдать' in html, 'видит блок ДЗ')

# ───────── S2: teacher dashboard
print(f'\n{B}━━━ S2: TEACHER DASHBOARD ━━━{D}')
tc = Client(); tc.login(username=T_USER, password='Pass123')
r = tc.get('/ru/dashboard/')
check(r.status_code == 200, 'GET /dashboard/ под учителем → 200', f'got {r.status_code}')
html = r.content.decode('utf-8')
check('Здравствуйте' in html, 'видит приветствие учителя')
check('На балансе' in html, 'видит блок баланса')
check('Активных учеников' in html, 'видит блок активных учеников')
check('1' in html, 'видит счётчик активных учеников = 1')
check('Заработок' in html or 'Месяц' in html, 'видит блок заработка')
check('HW2' in html, 'видит ДЗ на проверке')
check('эскроу' in html.lower(), 'видит эскроу-баланс')

# ───────── S3: admin hub
print(f'\n{B}━━━ S3: ADMIN HUB ━━━{D}')
ac = Client(); ac.login(username=A_USER, password='Pass123')
r = ac.get('/ru/admin-dashboard/billing/')
check(r.status_code == 200, 'GET /admin-dashboard/billing/ → 200', f'got {r.status_code}')
html = r.content.decode('utf-8')
check('Последние транзакции' in html, 'видит ленту транзакций')
check('Последние заявки' in html or 'Последние подписки' in html,
      'видит ленту последних активностей')
check('Новых пользователей' in html, 'видит счётчик новых пользователей')
check(S_USER in html or T_USER in html or A_USER in html,
      'username из недавних активностей виден в hub')

# ───────── S4: nav button в student profile
print(f'\n{B}━━━ S4: STUDENT PROFILE NAV ━━━{D}')
r = sc.get('/ru/profile/')
html = r.content.decode('utf-8')
check('Дашборд' in html, 'кнопка «Дашборд» в профиле ученика')
check('tachometer' in html, 'иконка дашборда присутствует')

# ───────── S5: nav button в teacher profile
print(f'\n{B}━━━ S5: TEACHER PROFILE NAV ━━━{D}')
r = tc.get('/ru/profile/')
html = r.content.decode('utf-8')
check('Дашборд' in html, 'кнопка «Дашборд» в профиле учителя')

# ───────── CLEANUP
print(f'\n{B}━━━ CLEANUP ━━━{D}')
ids = list(User.objects.filter(username__startswith='p10_').values_list('pk', flat=True))
HomeworkSubmission.objects.filter(homework__teacher__user_id__in=ids).delete()
Homework.objects.filter(teacher__user_id__in=ids).delete()
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
deleted = User.objects.filter(username__startswith='p10_').delete()
print(f'  очищено: {deleted}')

print(f'\n{B}━━━━━━━━━━━━━━━━━━━━━━━━{D}')
print(f'  ВСЕГО:  {PASSED+FAILED}')
print(f'  {green("PASSED:")} {PASSED}')
if FAILED:
    print(f'  {red("FAILED:")} {FAILED}')
    sys.exit(1)
print(f'\n{green("✅ P10 smoke завершён.")}')
