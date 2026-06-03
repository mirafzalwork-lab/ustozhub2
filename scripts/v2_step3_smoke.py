"""Smoke-тест v2 Шаг 3 — перенос урока (reschedule).

Сценарии (глазами УЧЕНИКА и УЧИТЕЛЯ):
  R1. Подписочный урок → переносится сразу в confirmed (оплаченный урок не теряется),
      старый слот свободен, новый booked, аудит reschedule_count/reschedules_used.
  R2. Дедлайн: перенос ближе чем за RESCHEDULE_MIN_LEAD_HOURS до начала → отказ.
  R3. Месячный лимит переносов на подписку → после N → отказ.
  R4. Разовый урок (без подписки) → переносится в pending+hold (учитель подтверждает).

Запуск: python scripts/v2_step3_smoke.py
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
from django.utils import timezone  # noqa: E402

from teachers.models import Booking, TimeSlot  # noqa: E402
from billing.models import Subscription, Transaction, Wallet  # noqa: E402
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


PREFIX = 'v2s3_'
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


def make_setup(tag):
    teacher, subject = _make_teacher_with_subject(f'{PREFIX}t_{tag}')
    tariff = _make_tariff(teacher, subject, lessons_per_week=2,
                          duration_months=1, price=Decimal('800000'))
    student = _make_student_with_balance(f'{PREFIX}s_{tag}', balance=Decimal('1000000'))
    sub = SubscriptionService.purchase(
        student=student, tariff=tariff,
        idempotency_key=f'{PREFIX}buy_{tag}_{uuid.uuid4().hex[:6]}',
    )
    return teacher, student, sub


def free_slot(teacher, days):
    start = timezone.now() + timedelta(days=days)
    return TimeSlot.objects.create(
        teacher=teacher, start_at=start,
        end_at=start + timedelta(minutes=60), status='free',
    )


print(f'\n{B}━━━━━━ v2 ШАГ 3: ПЕРЕНОС УРОКА ━━━━━━{D}')
print(f'  (RESCHEDULE_MIN_LEAD_HOURS={settings.RESCHEDULE_MIN_LEAD_HOURS}, '
      f'лимит/мес={settings.SUBSCRIPTION_FREE_RESCHEDULES_PER_MONTH})')
cleanup()

# ===== R1: подписочный урок → остаётся confirmed =====
sect('R1. Подписочный урок переносится в confirmed (оплаченный урок не теряется)')
teacher, student, sub = make_setup('r1')
b = Booking.objects.filter(subscription=sub).select_related('slot').first()
b.slot.start_at = timezone.now() + timedelta(days=5)
b.slot.end_at = b.slot.start_at + timedelta(minutes=60)
b.slot.status = 'booked'
b.slot.save()
b.status = 'confirmed'
b.save()
old_slot_id = b.slot_id
target = free_slot(teacher, days=6)
role('ученик', 'переносит подтверждённый урок на другое свободное время')
new_status = b.reschedule_by_student(target.id)
b.refresh_from_db(); sub.refresh_from_db()
old = TimeSlot.objects.get(pk=old_slot_id); target.refresh_from_db()
expect(new_status == 'confirmed', 'R1: урок остался confirmed (без повторного подтверждения)', new_status)
expect(b.slot_id == target.id, 'R1: бронь переехала на новый слот')
expect(target.status == 'booked', 'R1: новый слот → booked', target.status)
expect(old.status == 'free', 'R1: старый слот освобождён', old.status)
expect(b.reschedule_count == 1, 'R1: reschedule_count=1', str(b.reschedule_count))
expect(b.rescheduled_at is not None, 'R1: rescheduled_at проставлен')
expect(sub.reschedules_used == 1, 'R1: reschedules_used подписки=1', str(sub.reschedules_used))
role('учитель', 'видит урок в новое время, статус подтверждён — заново подтверждать не нужно')

# ===== R2: дедлайн =====
sect('R2. Перенос слишком близко к началу → отказ')
teacher, student, sub = make_setup('r2')
b = Booking.objects.filter(subscription=sub).select_related('slot').first()
b.slot.start_at = timezone.now() + timedelta(hours=1)  # < 4ч
b.slot.end_at = b.slot.start_at + timedelta(minutes=60)
b.slot.status = 'booked'
b.slot.save()
b.status = 'confirmed'
b.save()
target = free_slot(teacher, days=3)
try:
    b.reschedule_by_student(target.id)
    blocked = False
except ValueError as e:
    blocked = True; msg = str(e)
expect(blocked, 'R2: перенос за 1ч до урока отклонён (дедлайн)')
role('ученик', f'видит ошибку: «{msg if blocked else ""}»')

# ===== R3: месячный лимит =====
sect('R3. Месячный лимит переносов на подписку')
teacher, student, sub = make_setup('r3')
b = Booking.objects.filter(subscription=sub).select_related('slot').first()
b.slot.start_at = timezone.now() + timedelta(days=5)
b.slot.end_at = b.slot.start_at + timedelta(minutes=60)
b.slot.status = 'booked'; b.slot.save()
b.status = 'confirmed'; b.save()
limit = settings.SUBSCRIPTION_FREE_RESCHEDULES_PER_MONTH
targets = [free_slot(teacher, days=10 + i) for i in range(limit + 1)]
ok_count = 0
for i in range(limit):
    b.reschedule_by_student(targets[i].id)
    b.refresh_from_db()
    ok_count += 1
expect(ok_count == limit, f'R3: {limit} переносов прошли успешно', str(ok_count))
try:
    b.reschedule_by_student(targets[limit].id)
    over = False
except ValueError as e:
    over = True; msg3 = str(e)
expect(over, f'R3: перенос №{limit + 1} отклонён (лимит исчерпан)')
sub.refresh_from_db()
expect(sub.reschedules_used == limit, f'R3: reschedules_used={limit}', str(sub.reschedules_used))
role('ученик', f'видит: «{msg3 if over else ""}»')

# ===== R4: разовый урок (без подписки) → pending+hold =====
sect('R4. Разовый урок (без подписки) → pending + hold (учитель подтверждает)')
teacher, subject = _make_teacher_with_subject(f'{PREFIX}t_r4')
student = _make_student_with_balance(f'{PREFIX}s_r4', balance=Decimal('0'))
start = timezone.now() + timedelta(days=4)
slot = TimeSlot.objects.create(teacher=teacher, start_at=start,
                               end_at=start + timedelta(minutes=60), status='booked')
oneoff = Booking.objects.create(slot=slot, student=student, subject=subject,
                                status='confirmed', is_trial=False)
target = free_slot(teacher, days=7)
new_status = oneoff.reschedule_by_student(target.id)
oneoff.refresh_from_db(); target.refresh_from_db()
expect(new_status == 'pending', 'R4: разовый урок → pending (ждёт подтверждения)', new_status)
expect(target.status == 'held', 'R4: новый слот → held', target.status)
role('учитель', 'получает разовый перенос как заявку — должен подтвердить')

# ===== Итог =====
cleanup()
sect('ИТОГ')
print(f'  {G}PASSED: {len(PASSED)}{D}   {R if FAILED else G}FAILED: {len(FAILED)}{D}')
if FAILED:
    for n, d in FAILED:
        print(f'    {R}✗ {n}{D}' + (f' — {d}' if d else ''))
    sys.exit(1)
print(f'\n{G}Все сценарии Шага 3 пройдены.{D}')
