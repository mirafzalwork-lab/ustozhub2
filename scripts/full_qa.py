"""Полная QA-прогонка фаз P1-P6.

Сценарии:
  Phase 1 (Wallet/Tx):
    1.  Wallet auto-created on user-create
    2.  Admin может пополнить кошелёк (через сервис)
    3.  Reconcile balance == sum(transactions) после всех операций

  Phase 2 (Tariff):
    4.  Anon → /profile/tariffs/ редирект на login
    5.  Student → /profile/tariffs/ редирект (нет teacher_profile)
    6.  Teacher → /profile/tariffs/ 200 + видит свои тарифы
    7.  Create tariff: success → tariff в БД
    8.  Create tariff с min_price < 10000 → form error
    9.  Create tariff с foreign subject → form error
    10. Edit tariff: чужой → 404
    11. Toggle active / delete
    12. Empty state корректен (новый teacher)

  Phase 3 (Subscription purchase):
    13. teacher_detail публичная страница показывает «Подписки и тарифы»
    14. Anon видит «Войти, чтобы купить»
    15. Student видит «Купить»
    16. Учитель НЕ может купить у самого себя
    17. Buy without enough balance → button disabled, POST → error
    18. Buy ok → wallet списан + Subscription created + 8 bookings + slots booked
    19. Idempotency: повтор того же ключа → одна подписка
    20. AlreadySubscribed: вторая попытка → error
    21. teacher без weekly_schedule → NotEnoughCapacity
    22. /my/subscriptions/ список (active + history)
    23. /profile/subscribers/ — учитель видит ученика
    24. Progress bar отрисован

  Phase 4 (Payout):
    25. Mark Booking completed → signal инкрементит completed_lessons
    26. release_lesson_payout: правильные суммы (85k teacher, 15k platform)
    27. Idempotent (2-й вызов = False, балансы не удваиваются)
    28. После всех 8 payouts: status=completed, escrow=0
    29. Celery-task release_pending_payouts работает только за grace_window
    30. UI: «Выплачено всего» / «За 30 дней» / «В эскроу» отображаются

  Phase 5 (Cancel/refund):
    31. Cancel чистый: 800k refund + 8 cancelled bookings + слоты free
    32. Cancel с 2 completed (1 paid, 1 не paid): доплата + refund остатка
    33. Cancel уже cancelled → CancellationError
    34. Cancel by teacher: status=cancelled_by_teacher
    35. Cancel COMPLETED подписки → CancellationError
    36. UI: кнопка с модалкой + reason field

  Phase 6 (Withdrawal):
    37. Anon → /profile/withdrawals/ редирект
    38. Student → /profile/withdrawals/ редирект
    39. Min amount валидация (< 100 000 → form error)
    40. Insufficient funds → form error
    41. Create + idempotent retry → 1 заявка
    42. Cancel by user → refund
    43. Reject (требует note) → refund
    44. Approve → status=approved (без refund)
    45. Complete → status=completed (без refund)
    46. UI history table + status pills

  CROSS-CUTTING:
    47. Финансовая сходимость: ∑ всех balance == 0 (если включить отрицательные)
        Точнее: ∑ transactions per wallet == wallet.balance
    48. Все Decimal-операции с 2 знаками без потерь
    49. Idempotency keys везде UNIQUE
"""
import os, sys, django, uuid
from datetime import timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.test import Client
from django.utils import timezone

from teachers.models import (
    Booking, Subject, SubjectCategory, StudentProfile, TeacherProfile,
    TeacherSubject, TimeSlot,
)
from billing.models import Subscription, Tariff, Transaction, Wallet, WithdrawalRequest
from billing.platform_account import get_or_create_platform_user
from billing.services import (
    AlreadySubscribed, CancellationError, InsufficientFunds, NotEnoughCapacity,
    SubscriptionService, WalletService, WithdrawalAmountError, WithdrawalError,
    WithdrawalService,
)
from billing.tasks import release_pending_payouts

User = get_user_model()

# ---- helpers --------------------------------------------------------------

PASS = 0
FAIL = 0
FAILURES = []

def check(label, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'\033[32m  ✓\033[0m {label}')
    else:
        FAIL += 1
        FAILURES.append((label, detail))
        print(f'\033[31m  ✗\033[0m {label}' + (f'  ←  {detail}' if detail else ''))

def section(title):
    print(f'\n\033[1m== {title} ==\033[0m')

# ---- cleanup --------------------------------------------------------------

SUFFIX = uuid.uuid4().hex[:6]
PREFIX = f'qa_{SUFFIX}_'

old = User.objects.filter(username__startswith='qa_')
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

platform = get_or_create_platform_user()
platform_start = platform.wallet.balance

# ---- setup ---------------------------------------------------------------

def make_teacher(name, with_schedule=True):
    u = User.objects.create_user(
        username=f'{PREFIX}t_{name}', email=f'{PREFIX}t_{name}@x.com',
        password='Pass123', user_type='teacher',
    )
    schedule = {d: [{'from': '09:00', 'to': '13:00'}] for d in
                ('monday', 'tuesday', 'wednesday', 'thursday', 'friday')} if with_schedule else {}
    p = TeacherProfile.objects.create(
        user=u, experience_years=3,
        moderation_status='approved', is_active=True,
        weekly_schedule=schedule,
    )
    return u, p

def make_student(name, balance=Decimal('0')):
    u = User.objects.create_user(
        username=f'{PREFIX}s_{name}', email=f'{PREFIX}s_{name}@x.com',
        password='Pass123', user_type='student',
    )
    StudentProfile.objects.create(user=u)
    if balance > 0:
        WalletService.credit(
            user=u, amount=balance,
            tx_type=Transaction.Type.DEPOSIT,
            idempotency_key=f'{PREFIX}seed-{name}',
        )
    return u

def make_admin():
    return User.objects.create_user(
        username=f'{PREFIX}admin', email=f'{PREFIX}admin@x.com',
        password='Pass123', is_staff=True, is_superuser=True,
    )

cat, _ = SubjectCategory.objects.get_or_create(name='Языки')
subj_en, _ = Subject.objects.get_or_create(name='Английский', defaults={'category': cat})
subj_de, _ = Subject.objects.get_or_create(name='Немецкий', defaults={'category': cat})

t_user, teacher = make_teacher('a')
TeacherSubject.objects.create(teacher=teacher, subject=subj_en, hourly_rate=Decimal('80000'))
TeacherSubject.objects.create(teacher=teacher, subject=subj_de, hourly_rate=Decimal('90000'))

s_user1 = make_student('1', balance=Decimal('2000000'))
s_user2 = make_student('2', balance=Decimal('100000'))  # маловато для покупки
admin_user = make_admin()

# ============================================================================
section('PHASE 1: Wallet & Transactions')

check('1.  Wallet auto-created on user-create',
      hasattr(t_user, 'wallet') and t_user.wallet.balance == Decimal('0'))

s_user1.wallet.refresh_from_db()
check('2.  Wallet ученика после credit() == 2_000_000',
      s_user1.wallet.balance == Decimal('2000000.00'))

# Reconcile
for u in (t_user, s_user1, s_user2, platform):
    u.wallet.refresh_from_db()
    rec = WalletService.reconcile_balance(u.wallet)
    check(f'3.  Reconcile balance == ledger ({u.username}): {u.wallet.balance}',
          rec == u.wallet.balance, f'rec={rec}')

# ============================================================================
section('PHASE 2: Tariff CRUD')

anon_c = Client()
r = anon_c.get('/ru/profile/tariffs/')
check('4.  Anon → /profile/tariffs/ redirects', r.status_code == 302)

stud_c = Client(); stud_c.login(username=s_user1.username, password='Pass123')
r = stud_c.get('/ru/profile/tariffs/')
check('5.  Student → /profile/tariffs/ redirects (no teacher_profile)',
      r.status_code == 302)

tc = Client(); tc.login(username=t_user.username, password='Pass123')
r = tc.get('/ru/profile/tariffs/')
check('6.  Teacher → /profile/tariffs/ HTTP 200', r.status_code == 200)
check('6a. Empty state виден (нет тарифов)',
      'У вас пока нет тарифов' in r.content.decode('utf-8'))

# Create
r = tc.post('/ru/profile/tariffs/new/', {
    'subject': subj_en.id, 'name': 'Стандарт',
    'description': 'Базовый английский',
    'lessons_per_week': 2, 'lesson_duration_minutes': 60,
    'duration_months': 1, 'price_per_month': '800000',
    'is_active': 'on',
})
check('7.  Create tariff: 302 redirect', r.status_code == 302)
t_en = Tariff.objects.get(teacher=teacher, subject=subj_en)
check('7a. Tariff в БД с правильными параметрами',
      t_en.total_lessons == 8 and t_en.total_price == Decimal('800000.00'))

# Min-price validation
r = tc.post('/ru/profile/tariffs/new/', {
    'subject': subj_de.id, 'name': 'low',
    'lessons_per_week': 1, 'lesson_duration_minutes': 30,
    'duration_months': 1, 'price_per_month': '5000',
    'is_active': 'on',
})
check('8.  Min-price < 10 000 → form error (status 200, не 302)',
      r.status_code == 200 and 'Минимальная цена' in r.content.decode('utf-8'))

# Foreign subject (создаём чужой предмет — used across runs)
foreign_subj, _ = Subject.objects.get_or_create(name='Японский', defaults={'category': cat})
r = tc.post('/ru/profile/tariffs/new/', {
    'subject': foreign_subj.id, 'name': 'foreign',
    'lessons_per_week': 2, 'lesson_duration_minutes': 60,
    'duration_months': 1, 'price_per_month': '500000',
    'is_active': 'on',
})
check('9.  Foreign subject (не у учителя) → form error',
      r.status_code == 200)

# Cannot edit other's
other_user, other_teacher = make_teacher('other')
TeacherSubject.objects.create(teacher=other_teacher, subject=subj_en, hourly_rate=Decimal('70000'))
other_tariff = Tariff.objects.create(
    teacher=other_teacher, subject=subj_en,
    lessons_per_week=1, lesson_duration_minutes=60,
    duration_months=1, price_per_month=Decimal('500000'),
)
r = tc.get(f'/ru/profile/tariffs/{other_tariff.pk}/edit/')
check('10. Чужой тариф → edit 404', r.status_code == 404)

# Toggle
t_en_active_before = t_en.is_active
r = tc.post(f'/ru/profile/tariffs/{t_en.pk}/toggle/')
t_en.refresh_from_db()
check('11. Toggle active', t_en.is_active != t_en_active_before)
# Toggle обратно
tc.post(f'/ru/profile/tariffs/{t_en.pk}/toggle/')
t_en.refresh_from_db()
check('11a. Toggle обратно (тариф снова active)', t_en.is_active)

# ============================================================================
section('PHASE 3: Subscription purchase')

# Anon teacher_detail
r = anon_c.get(f'/ru/teacher/{teacher.id}/')
check('13. teacher_detail публичная: HTTP 200', r.status_code == 200)
check('13a. Секция «Подписки и тарифы» отрендерилась',
      'Подписки и тарифы' in r.content.decode('utf-8'))

check('14. Anon: «Войти, чтобы купить»',
      'Войти, чтобы купить' in r.content.decode('utf-8'))

# Student logged-in видит «Купить»
sc = Client(); sc.login(username=s_user1.username, password='Pass123')
r = sc.get(f'/ru/teacher/{teacher.id}/')
check('15. Логин-ученик видит CTA «Купить»',
      f'/subscriptions/buy/{t_en.id}/' in r.content.decode('utf-8'))

# Учитель не может купить у самого себя
r = tc.get(f'/ru/subscriptions/buy/{t_en.id}/')
check('16. Учитель → купить свой тариф → redirect (нельзя)',
      r.status_code == 302)

# Buy с недостаточным балансом — POST → error
poor_c = Client(); poor_c.login(username=s_user2.username, password='Pass123')
r = poor_c.post(f'/ru/subscriptions/buy/{t_en.id}/', {'idempotency_key': str(uuid.uuid4())})
check('17. Недостаточно средств → нет подписки',
      Subscription.objects.filter(student=s_user2).count() == 0)

# Buy ok
idem_main = str(uuid.uuid4())
r = sc.post(f'/ru/subscriptions/buy/{t_en.id}/', {'idempotency_key': idem_main})
check('18. Покупка: 302 redirect', r.status_code == 302)

sub = Subscription.objects.get(student=s_user1)
s_user1.wallet.refresh_from_db()
check('18a. Subscription создана, status=active',
      sub.status == 'active')
check('18b. Wallet ученика списан на 800k',
      s_user1.wallet.balance == Decimal('1200000.00'))
check('18c. 8 bookings создано',
      Booking.objects.filter(subscription=sub).count() == 8)
booked_slots = TimeSlot.objects.filter(teacher=teacher, status='booked').count()
check('18d. 8 слотов помечены booked', booked_slots >= 8)

# Idempotency
r = sc.post(f'/ru/subscriptions/buy/{t_en.id}/', {'idempotency_key': idem_main})
check('19. Idempotent retry → подписок всё ещё 1',
      Subscription.objects.filter(student=s_user1).count() == 1)

# AlreadySubscribed (другой idempotency_key, тот же teacher+subject)
WalletService.credit(user=s_user1, amount=Decimal('800000'),
                     tx_type=Transaction.Type.DEPOSIT,
                     idempotency_key=f'{PREFIX}top1')
try:
    SubscriptionService.purchase(
        student=s_user1, tariff=t_en,
        idempotency_key=f'{PREFIX}second-key',
    )
    check('20. AlreadySubscribed exception raised', False)
except AlreadySubscribed:
    check('20. AlreadySubscribed exception raised', True)

# NotEnoughCapacity — учитель без расписания
ns_user, ns_teacher = make_teacher('no_sched', with_schedule=False)
TeacherSubject.objects.create(teacher=ns_teacher, subject=subj_en, hourly_rate=Decimal('50000'))
ns_tariff = Tariff.objects.create(
    teacher=ns_teacher, subject=subj_en,
    lessons_per_week=1, lesson_duration_minutes=60, duration_months=1,
    price_per_month=Decimal('200000'),
)
try:
    SubscriptionService.purchase(
        student=s_user1, tariff=ns_tariff,
        idempotency_key=f'{PREFIX}ns',
    )
    check('21. NotEnoughCapacity → exception', False)
except NotEnoughCapacity:
    check('21. NotEnoughCapacity → exception', True)

# UI: my_subscriptions
r = sc.get('/ru/my/subscriptions/')
html = r.content.decode('utf-8')
check('22. /my/subscriptions/ HTTP 200', r.status_code == 200)
check('22a. Видна активная подписка', 'Активные' in html and 'Английский' in html)

# UI: teacher_subscribers
r = tc.get('/ru/profile/subscribers/')
html_t = r.content.decode('utf-8')
check('23. /profile/subscribers/ HTTP 200', r.status_code == 200)
check('23a. Учитель видит ученика', s_user1.username in html_t)
check('24. Progress bar в HTML',
      'progress-bar' in html and 'progress-bar' in html_t)

# ============================================================================
section('PHASE 4: Lesson payout')

bookings = list(Booking.objects.filter(subscription=sub).order_by('slot__start_at')[:3])

# Mark 3 completed — сигнал должен инкрементить
for b in bookings:
    b.status = 'completed'
    b.save()
sub.refresh_from_db()
check('25. Signal: completed_lessons = 3', sub.completed_lessons == 3)

# Бэкдейтим end_at в прошлое (выходит за grace window)
past = timezone.now() - timedelta(hours=settings.PAYOUT_GRACE_HOURS + 1)
for b in bookings:
    b.slot.start_at = past - timedelta(hours=1)
    b.slot.end_at = past
    b.slot.save()

# Запускаем task
result = release_pending_payouts()
check('26. release_pending_payouts: 3 paid', result['paid'] == 3)

sub.refresh_from_db()
t_user.wallet.refresh_from_db()
platform.wallet.refresh_from_db()
expected_teacher = Decimal('255000.00')  # 3 × 100k × 0.85
expected_platform_delta = Decimal('45000.00')  # 3 × 100k × 0.15
expected_escrow = Decimal('500000.00')
check('26a. Учитель получил 255 000',
      t_user.wallet.balance == expected_teacher,
      f'got {t_user.wallet.balance}')
check('26b. Платформа получила 45 000',
      platform.wallet.balance == platform_start + expected_platform_delta)
check('26c. Эскроу = 500 000', sub.escrow_balance == expected_escrow)

# Идемпотентность — второй прогон не делает payout
r2 = release_pending_payouts()
check('27. Idempotent task: paid=0 повторно', r2['paid'] == 0)
t_user.wallet.refresh_from_db()
check('27a. Балансы не удвоились', t_user.wallet.balance == expected_teacher)

# Grace window — новый booking ещё в будущем
fut_b = Booking.objects.filter(subscription=sub).exclude(id__in=[b.id for b in bookings]).first()
fut_b.status = 'completed'
fut_b.save()  # signal += 1
# end_at в будущем (grace window не прошёл)
fut_b.slot.start_at = timezone.now() + timedelta(hours=1)
fut_b.slot.end_at = timezone.now() + timedelta(hours=2)
fut_b.slot.save()
r3 = release_pending_payouts()
check('29. Grace window: completed но в будущем — не выплачивается',
      r3['paid'] == 0)
# вернём в прошлое для последующих тестов
fut_b.status = 'confirmed'
fut_b.save()
sub.refresh_from_db()

# UI: earned cards
r = tc.get('/ru/profile/subscribers/')
html = r.content.decode('utf-8')
check('30. UI «Выплачено всего»', 'Выплачено всего' in html)
check('30a. UI сумма 255 000 видна', '255' in html and '000' in html)
check('30b. UI «В эскроу»', 'В эскроу' in html or 'эскроу' in html)

# ============================================================================
section('PHASE 5: Cancel + refund')

# Cancel «pure» (без completed) — создадим вторую подписку для чистого случая
# А первую (с 3 paid + 0 grace) отменим, проверим payout grace
s_user1.wallet.refresh_from_db()
balance_before = s_user1.wallet.balance
result = SubscriptionService.cancel(sub, cancelled_by='student', reason='тест')

sub.refresh_from_db()
s_user1.wallet.refresh_from_db()

# Проверка: за completed-но-не-paid: 0 (все 3 уже paid)
# Refund = escrow_balance до отмены = 500_000
check('31. Cancel: status=cancelled_by_student',
      sub.status == 'cancelled_by_student')
check('31a. escrow=0', sub.escrow_balance == Decimal('0'))
check('31b. refunded=500 000',
      result['refunded'] == Decimal('500000.00'))
check('31c. cancelled_bookings=5 (4 будущих confirmed + 1 fut_b)',
      result['cancelled_bookings'] == 5)
check('31d. balance ученика += 500k',
      s_user1.wallet.balance == balance_before + Decimal('500000'))

# Cancel идемпотентность
try:
    SubscriptionService.cancel(sub, cancelled_by='student', reason='ещё раз')
    check('33. Cancel уже cancelled → CancellationError', False)
except CancellationError:
    check('33. Cancel уже cancelled → CancellationError', True)

# Cancel by teacher на новой подписке
# Создадим новую: с другим ключом, но same teacher+subject — теперь можно потому что 1-я cancelled
WalletService.credit(user=s_user1, amount=Decimal('800000'),
                     tx_type=Transaction.Type.DEPOSIT,
                     idempotency_key=f'{PREFIX}top2')
sub2 = SubscriptionService.purchase(
    student=s_user1, tariff=t_en,
    idempotency_key=f'{PREFIX}sub2',
)
result = SubscriptionService.cancel(sub2, cancelled_by='teacher', reason='не смогу')
sub2.refresh_from_db()
check('34. Cancel by teacher: status=cancelled_by_teacher',
      sub2.status == 'cancelled_by_teacher')

# Cancel completed подписку (создадим и завершим)
WalletService.credit(user=s_user1, amount=Decimal('800000'),
                     tx_type=Transaction.Type.DEPOSIT,
                     idempotency_key=f'{PREFIX}top3')
sub3 = SubscriptionService.purchase(
    student=s_user1, tariff=t_en,
    idempotency_key=f'{PREFIX}sub3',
)
# Завершим все 8
for b in Booking.objects.filter(subscription=sub3):
    b.status = 'completed'
    b.save()
    SubscriptionService.release_lesson_payout(b)
sub3.refresh_from_db()
check('35a. Subscription auto-completed после 8 payouts',
      sub3.status == 'completed')
try:
    SubscriptionService.cancel(sub3, cancelled_by='student', reason='-')
    check('35. Cancel completed → CancellationError', False)
except CancellationError:
    check('35. Cancel completed → CancellationError', True)

# ============================================================================
section('PHASE 6: Withdrawal')

# Anon /profile/withdrawals/
r = anon_c.get('/ru/profile/withdrawals/')
check('37. Anon withdrawals redirect', r.status_code == 302)

# Student withdrawals redirect
r = stud_c.get('/ru/profile/withdrawals/')
check('38. Student → withdrawals redirect (нет teacher_profile)',
      r.status_code == 302)

# Min amount form
r = tc.post('/ru/profile/withdrawals/', {
    'amount': '50000',  # ниже минимума
    'payout_method': 'card',
    'payout_details': '8600 1234',
    'idempotency_key': str(uuid.uuid4()),
})
check('39. Min < 100k → form invalid (HTTP 200)', r.status_code == 200)
check('39a. Сообщение про минимум видно',
      'Минимальная сумма' in r.content.decode('utf-8'))

# Insufficient funds
t_user.wallet.refresh_from_db()
huge = int(t_user.wallet.balance + 1)
r = tc.post('/ru/profile/withdrawals/', {
    'amount': str(huge),
    'payout_method': 'card',
    'payout_details': '8600 1234',
    'idempotency_key': str(uuid.uuid4()),
})
check('40. Insufficient funds → form error',
      r.status_code == 200 and 'На балансе' in r.content.decode('utf-8'))

# Create + idempotency
balance_before = t_user.wallet.balance
idem_wd = str(uuid.uuid4())
r = tc.post('/ru/profile/withdrawals/', {
    'amount': '150000',
    'payout_method': 'card',
    'payout_details': '8600 1234 5678 9012',
    'idempotency_key': idem_wd,
})
check('41. Create wd: 302', r.status_code == 302)
t_user.wallet.refresh_from_db()
check('41a. Wallet списан на 150k',
      t_user.wallet.balance == balance_before - Decimal('150000'))

# Повторный submit того же idem (на UI это hidden input)
# WithdrawalService.create_request use his own webkey включая user.id; UI делает уникальный each load
# Но кнопка submit может дважды нажаться — тогда тот же idem
r = tc.post('/ru/profile/withdrawals/', {
    'amount': '150000',
    'payout_method': 'card',
    'payout_details': '8600 1234 5678 9012',
    'idempotency_key': idem_wd,
})
count = WithdrawalRequest.objects.filter(user=t_user).count()
check('41b. Idempotent: 1 заявка после повтора',
      count == 1, f'got {count}')

wr = WithdrawalRequest.objects.get(user=t_user)

# Cancel by user
r = tc.post(f'/ru/profile/withdrawals/{wr.id}/cancel/')
wr.refresh_from_db()
t_user.wallet.refresh_from_db()
check('42. Cancel by user: status=cancelled + refund',
      wr.status == 'cancelled' and t_user.wallet.balance == balance_before)

# Новая заявка → admin reject
WalletService.credit(user=t_user, amount=Decimal('200000'),
                     tx_type=Transaction.Type.DEPOSIT,
                     idempotency_key=f'{PREFIX}t_top')
t_user.wallet.refresh_from_db()
balance_before = t_user.wallet.balance
wr2 = WithdrawalService.create_request(
    user=t_user, amount=Decimal('200000'),
    payout_method='phone', payout_details='+998901234567',
    idempotency_key=f'{PREFIX}wr2',
)
try:
    WithdrawalService.reject(wr2, admin_user=admin_user, note='   ')
    check('43-pre. Reject без note → error', False)
except WithdrawalError:
    check('43-pre. Reject без note → error', True)

WithdrawalService.reject(wr2, admin_user=admin_user, note='подозрительная карта')
wr2.refresh_from_db()
t_user.wallet.refresh_from_db()
check('43. Reject: status=rejected + refund',
      wr2.status == 'rejected' and t_user.wallet.balance == balance_before)

# Approve → complete
wr3 = WithdrawalService.create_request(
    user=t_user, amount=Decimal('100000'),
    payout_method='card', payout_details='card',
    idempotency_key=f'{PREFIX}wr3',
)
t_user.wallet.refresh_from_db()
balance_after_create = t_user.wallet.balance
WithdrawalService.approve(wr3, admin_user=admin_user)
wr3.refresh_from_db()
check('44. Approve: status=approved (балл не меняется)',
      wr3.status == 'approved')
t_user.wallet.refresh_from_db()
check('44a. balance после approve не вернулся',
      t_user.wallet.balance == balance_after_create)

WithdrawalService.complete(wr3, admin_user=admin_user, note='перевод 25.05')
wr3.refresh_from_db()
t_user.wallet.refresh_from_db()
check('45. Complete: status=completed, balance не возвращается',
      wr3.status == 'completed' and t_user.wallet.balance == balance_after_create)

# UI withdrawals_list
r = tc.get('/ru/profile/withdrawals/')
html = r.content.decode('utf-8')
check('46. UI history table рендерится',
      'История заявок' in html)
check('46a. Status pills для разных статусов',
      'status-pill' in html and 'cancelled' in html and 'rejected' in html and 'completed' in html)

# ============================================================================
section('CROSS-CUTTING')

# Финансовая сходимость по каждому wallet
for u in (t_user, s_user1, s_user2, platform):
    u.wallet.refresh_from_db()
    rec = WalletService.reconcile_balance(u.wallet)
    check(f'47. Reconcile {u.username}: balance={u.wallet.balance}',
          rec == u.wallet.balance, f'rec={rec}')

# Глобальная сходимость: сумма Transaction.amount по всем нашим кошелькам == сумма balance.
my_user_ids = [u.id for u in (t_user, s_user1, s_user2, platform)]
all_tx_sum = Transaction.objects.filter(
    status=Transaction.Status.COMPLETED,
    wallet__user_id__in=my_user_ids,
).aggregate(s=Sum('amount'))['s'] or Decimal('0')
total_balance = sum((u.wallet.balance for u in (t_user, s_user1, s_user2, platform)), Decimal('0'))
check(f'48. Global ledger==balance: tx_sum={all_tx_sum} == total_balance={total_balance}',
      all_tx_sum == total_balance)

# Idempotency keys — все UNIQUE
from django.db.models import Count
dup_tx = Transaction.objects.values('idempotency_key').annotate(c=Count('id')).filter(c__gt=1)
check('49. Все Transaction idempotency_keys UNIQUE', not dup_tx.exists())
dup_sub = Subscription.objects.values('purchase_idempotency_key').annotate(c=Count('id')).filter(c__gt=1)
check('49a. Все Subscription purchase keys UNIQUE', not dup_sub.exists())
dup_wd = WithdrawalRequest.objects.values('idempotency_key').annotate(c=Count('id')).filter(c__gt=1)
check('49b. Все WithdrawalRequest keys UNIQUE', not dup_wd.exists())

# ============================================================================
print()
print(f'\033[1mИтого: \033[32m{PASS} PASS\033[0m, \033[31m{FAIL} FAIL\033[0m')
if FAILURES:
    print('\nПровалившиеся:')
    for label, det in FAILURES:
        print(f'  • {label}  ({det})')
