"""Smoke-тест v2 Шаг 6 — приостановка и возобновление подписки.

Сценарии (глазами УЧЕНИКА и УЧИТЕЛЯ):
  U1. pause активной подписки → PAUSED, будущие уроки сняты, слоты свободны,
      escrow не тронут (возврата нет), paused_at проставлен.
  U2. resume → ACTIVE, срок сдвинут на длительность паузы, расписание
      перегенерировано на оставшиеся уроки.
  U3. Ошибки: pause не-активной / resume не-приостановленной.
  U4. Денежный инвариант: pause/resume не двигают деньги.

Запуск: python scripts/v2_step6_smoke.py
"""
import os
import sys
import uuid
from datetime import timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
import django  # noqa: E402
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.db.models import Sum  # noqa: E402
from django.utils import timezone  # noqa: E402

from teachers.models import Booking, TimeSlot  # noqa: E402
from billing.models import Subscription, Transaction, Wallet  # noqa: E402
from billing.services import CancellationError, SubscriptionService  # noqa: E402
from billing.tests import (  # noqa: E402
    _make_teacher_with_subject, _make_tariff, _make_student_with_balance,
)

User = get_user_model()
G = '\033[32m'; R = '\033[31m'; Y = '\033[33m'; B = '\033[34m'; D = '\033[0m'

PASSED = []; FAILED = []
def expect(c, name, det=''):
    if c:
        PASSED.append(name); print(f'  {G}✓{D} {name}' + (f' — {det}' if det else ''))
    else:
        FAILED.append((name, det)); print(f'  {R}✗ {name}{D}' + (f' — {det}' if det else ''))
def sect(t): print(f'\n{B}━━━ {t} ━━━{D}')
def role(who, txt): print(f'   {Y}[{who}]{D} {txt}')


def total_money():
    w = Wallet.objects.aggregate(s=Sum('balance'))['s'] or Decimal('0')
    e = Subscription.objects.filter(
        status__in=Subscription.ACTIVE_STATUSES
    ).aggregate(s=Sum('escrow_balance'))['s'] or Decimal('0')
    return w + e


PREFIX = 'v2s6_'
def cleanup():
    ids = list(User.objects.filter(username__startswith=PREFIX).values_list('pk', flat=True))
    if not ids:
        return
    Booking.objects.filter(student_id__in=ids).delete()
    Booking.objects.filter(slot__teacher__user_id__in=ids).delete()
    TimeSlot.objects.filter(teacher__user_id__in=ids).delete()
    Subscription.objects.filter(student_id__in=ids).delete()
    Transaction.objects.filter(wallet__user_id__in=ids).delete()
    from teachers.models import TeacherSubject, TeacherProfile, StudentProfile
    TeacherSubject.objects.filter(teacher__user_id__in=ids).delete()
    TeacherProfile.objects.filter(user_id__in=ids).delete()
    StudentProfile.objects.filter(user_id__in=ids).delete()
    Wallet.objects.filter(user_id__in=ids).delete()
    User.objects.filter(pk__in=ids).delete()


def setup(tag):
    teacher, subject = _make_teacher_with_subject(f'{PREFIX}t_{tag}')
    tariff = _make_tariff(teacher, subject, lessons_per_week=2,
                          duration_months=1, price=Decimal('800000'))
    student = _make_student_with_balance(f'{PREFIX}s_{tag}', balance=Decimal('1000000'))
    sub = SubscriptionService.purchase(
        student=student, tariff=tariff,
        idempotency_key=f'{PREFIX}buy_{tag}_{uuid.uuid4().hex[:6]}',
    )
    return teacher, student, sub


print(f'\n{B}━━━━━━ v2 ШАГ 6: PAUSE / RESUME ━━━━━━{D}')
cleanup()

# ===== U1 + U2 + U4: полный цикл pause→resume =====
sect('U1. Приостановка активной подписки')
teacher, student, sub = setup('u1')
# Делаем 2 урока завершёнными (в прошлом) → completed_lessons=2.
done = list(Booking.objects.filter(subscription=sub).order_by('slot__start_at'))[:2]
for b in done:
    b.slot.start_at = timezone.now() - timedelta(days=2)
    b.slot.end_at = timezone.now() - timedelta(days=2, hours=-1)
    b.slot.save()
    b.status = 'completed'
    b.save()
sub.refresh_from_db()
escrow0 = sub.escrow_balance
money0 = total_money()
total0 = sub.total_lessons
completed0 = sub.completed_lessons
role('ученик', f'подписка активна: {completed0} уроков проведено, escrow {escrow0}')

freed = SubscriptionService.pause(sub, reason='отпуск')
sub.refresh_from_db()
future_active = Booking.objects.filter(
    subscription=sub, status__in=('confirmed', 'pending')).count()
free_slots = TimeSlot.objects.filter(teacher=teacher, status='free').count()
expect(sub.status == Subscription.Status.PAUSED, 'U1: статус PAUSED', sub.status)
expect(sub.paused_at is not None, 'U1: paused_at проставлен')
expect(freed == total0 - completed0, f'U1: снято {total0 - completed0} будущих уроков', str(freed))
expect(future_active == 0, 'U1: активных будущих броней не осталось', str(future_active))
expect(free_slots >= freed, 'U1: слоты освобождены', str(free_slots))
expect(sub.escrow_balance == escrow0, 'U1: escrow не тронут (возврата нет)', str(sub.escrow_balance))
expect(total_money() == money0, 'U1: денежный инвариант вокруг pause', f'{money0}→{total_money()}')
role('учитель', 'календарь на время паузы освободился')

sect('U2. Возобновление подписки')
# Эмулируем, что пауза длилась 3 дня — двигаем paused_at в прошлое.
exp_before = sub.expires_at
sub.paused_at = timezone.now() - timedelta(days=3)
sub.save(update_fields=['paused_at'])
money1 = total_money()
created = SubscriptionService.resume(sub)
sub.refresh_from_db()
future_active = Booking.objects.filter(
    subscription=sub, status__in=('confirmed', 'pending')).count()
expect(sub.status == Subscription.Status.ACTIVE, 'U2: статус ACTIVE', sub.status)
expect(sub.paused_at is None, 'U2: paused_at сброшен')
expect(created == total0 - completed0, f'U2: перегенерировано {total0 - completed0} уроков', str(created))
expect(future_active == created, 'U2: будущие брони восстановлены', str(future_active))
shift_days = (sub.expires_at - exp_before).days
expect(2 <= shift_days <= 3, 'U2: срок сдвинут на ~3 дня паузы', f'{shift_days}д')
expect(total_money() == money1, 'U2: денежный инвариант вокруг resume', f'{money1}→{total_money()}')
role('ученик', f'подписка снова активна: {created} уроков запланировано, срок продлён')

# ===== U3: ошибки =====
sect('U3. Ошибки переходов')
teacher, student, sub = setup('u3')
SubscriptionService.pause(sub)
try:
    SubscriptionService.pause(sub)  # уже на паузе
    e1 = False
except CancellationError:
    e1 = True
expect(e1, 'U3: повторный pause приостановленной → ошибка')
SubscriptionService.resume(sub)
try:
    SubscriptionService.resume(sub)  # уже активна
    e2 = False
except CancellationError:
    e2 = True
expect(e2, 'U3: resume активной → ошибка')

# ===== Итог =====
cleanup()
sect('ИТОГ')
print(f'  {G}PASSED: {len(PASSED)}{D}   {R if FAILED else G}FAILED: {len(FAILED)}{D}')
if FAILED:
    for n, d in FAILED:
        print(f'    {R}✗ {n}{D}' + (f' — {d}' if d else ''))
    sys.exit(1)
print(f'\n{G}Все сценарии Шага 6 пройдены.{D}')
