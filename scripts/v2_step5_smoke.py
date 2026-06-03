"""Smoke-тест v2 Шаг 5 — политика отмены урока с дедлайном.

Сценарии (глазами УЧЕНИКА и УЧИТЕЛЯ):
  P1. Ученик отменяет заблаговременно (> порога) → полный возврат, урок в квоту.
  P2. Ученик отменяет поздно (≤ порога) → урок списан, штраф учителю, возврата нет.
  P3. Учитель отменяет поздно → полный возврат ученику (вина учителя).
  P4. Денежный инвариант вокруг каждой отмены сохраняется.

Запуск: python scripts/v2_step5_smoke.py
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

from django.conf import settings  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.db.models import Sum  # noqa: E402
from django.utils import timezone  # noqa: E402

from teachers.models import Booking, TimeSlot  # noqa: E402
from billing.models import Subscription, Transaction, Wallet  # noqa: E402
from billing.platform_account import get_or_create_platform_user  # noqa: E402
from billing.services import SubscriptionService  # noqa: E402
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


PREFIX = 'v2s5_'
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


def set_start(booking, when):
    booking.slot.start_at = when
    booking.slot.end_at = when + timedelta(minutes=60)
    booking.slot.status = 'booked'
    booking.slot.save()
    booking.status = 'confirmed'
    booking.save()


print(f'\n{B}━━━━━━ v2 ШАГ 5: ПОЛИТИКА ОТМЕН ━━━━━━{D}')
print(f'  (CANCELLATION_FULL_REFUND_HOURS={settings.CANCELLATION_FULL_REFUND_HOURS})')
cleanup()
thr = settings.CANCELLATION_FULL_REFUND_HOURS

# ===== P1: ученик отменяет заблаговременно → полный возврат =====
sect('P1. Ученик отменяет заблаговременно → полный возврат, урок в квоту')
teacher, student, sub = setup('p1')
b = Booking.objects.filter(subscription=sub).select_related('slot').first()
set_start(b, timezone.now() + timedelta(hours=thr + 24))  # сильно заранее
student.wallet.refresh_from_db(); bal0 = student.wallet.balance
total0 = sub.total_lessons
money0 = total_money()
b.cancel_by_student(); b.refresh_from_db()
res = SubscriptionService.cancel_lesson(b, cancelled_by='student', reason='заранее')
sub.refresh_from_db(); student.wallet.refresh_from_db()
expect(res['policy'] == 'student_full_refund', 'P1: политика student_full_refund', res['policy'])
expect(res['refunded'] == Decimal('100000.00'), 'P1: возврат 100k', str(res['refunded']))
expect(student.wallet.balance - bal0 == Decimal('100000.00'), 'P1: баланс ученика +100k')
expect(sub.total_lessons == total0 - 1, 'P1: пакет уменьшился на 1', str(sub.total_lessons))
expect(total_money() == money0, 'P1: денежный инвариант', f'{money0}→{total_money()}')
role('ученик', 'отменил за сутки+ → деньги вернулись, урок снова доступен')

# ===== P2: ученик отменяет поздно → штраф учителю =====
sect('P2. Ученик отменяет поздно → урок списан, штраф учителю')
teacher, student, sub = setup('p2')
b = Booking.objects.filter(subscription=sub).select_related('slot').first()
set_start(b, timezone.now() + timedelta(hours=2))  # < порога
student.wallet.refresh_from_db(); s0 = student.wallet.balance
teacher.user.wallet.refresh_from_db(); t0 = teacher.user.wallet.balance
platform = get_or_create_platform_user(); platform.wallet.refresh_from_db(); p0 = platform.wallet.balance
escrow0 = sub.escrow_balance
money0 = total_money()
b.cancel_by_student(); b.refresh_from_db()
res = SubscriptionService.cancel_lesson(b, cancelled_by='student', reason='поздно')
sub.refresh_from_db(); student.wallet.refresh_from_db()
teacher.user.wallet.refresh_from_db(); platform.wallet.refresh_from_db()
expect(res['policy'] == 'student_late_charge', 'P2: политика student_late_charge', res['policy'])
expect(res['charged'] is True and res['refunded'] == Decimal('0.00'), 'P2: возврата нет, урок списан')
expect(student.wallet.balance == s0, 'P2: баланс ученика не изменился')
expect(teacher.user.wallet.balance - t0 == Decimal('85000.00'), 'P2: учителю штраф 85k',
       str(teacher.user.wallet.balance - t0))
expect(platform.wallet.balance - p0 == Decimal('15000.00'), 'P2: платформе 15k')
expect(escrow0 - sub.escrow_balance == Decimal('100000.00'), 'P2: escrow -100k')
expect(total_money() == money0, 'P2: денежный инвариант', f'{money0}→{total_money()}')
role('учитель', 'ученик отменил за 2ч → урок засчитан, штраф 85k получен')
role('ученик', 'поздняя отмена → урок сгорел, возврата нет')

# ===== P3: учитель отменяет поздно → полный возврат ученику =====
sect('P3. Учитель отменяет поздно → полный возврат ученику (вина учителя)')
teacher, student, sub = setup('p3')
b = Booking.objects.filter(subscription=sub).select_related('slot').first()
set_start(b, timezone.now() + timedelta(hours=2))  # < порога
student.wallet.refresh_from_db(); s0 = student.wallet.balance
teacher.user.wallet.refresh_from_db(); t0 = teacher.user.wallet.balance
money0 = total_money()
b.cancel_by_teacher(); b.refresh_from_db()
res = SubscriptionService.cancel_lesson(b, cancelled_by='teacher', reason='учитель занят')
sub.refresh_from_db(); student.wallet.refresh_from_db(); teacher.user.wallet.refresh_from_db()
expect(res['policy'] == 'teacher_full_refund', 'P3: политика teacher_full_refund', res['policy'])
expect(res['refunded'] == Decimal('100000.00'), 'P3: возврат ученику 100k', str(res['refunded']))
expect(student.wallet.balance - s0 == Decimal('100000.00'), 'P3: баланс ученика +100k')
expect(teacher.user.wallet.balance == t0, 'P3: учитель ничего не получил')
expect(total_money() == money0, 'P3: денежный инвариант', f'{money0}→{total_money()}')
role('учитель', 'отменил поздно → ученику полный возврат, штрафа ученику нет')

# ===== Итог =====
cleanup()
sect('ИТОГ')
print(f'  {G}PASSED: {len(PASSED)}{D}   {R if FAILED else G}FAILED: {len(FAILED)}{D}')
if FAILED:
    for n, d in FAILED:
        print(f'    {R}✗ {n}{D}' + (f' — {d}' if d else ''))
    sys.exit(1)
print(f'\n{G}Все сценарии Шага 5 пройдены.{D}')
