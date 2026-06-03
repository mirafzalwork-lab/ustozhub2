"""Smoke-тест v2 Шаг 4 — недельная квота уроков при переносе.

Дыра, которую закрываем: перенос мог стащить >N уроков в одну неделю, обходя тариф.

Сценарии (глазами УЧЕНИКА):
  Q1. book_schedule валидирует длину паттерна (= lessons_per_week).
  Q2. Перенос в неделю, где уже максимум уроков по тарифу → отказ.
  Q3. Перенос в неделю со свободным местом → успех.

Запуск: python scripts/v2_step4_smoke.py
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


PREFIX = 'v2s4_'
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


def setup(tag, lpw=2):
    teacher, subject = _make_teacher_with_subject(f'{PREFIX}t_{tag}')
    tariff = _make_tariff(teacher, subject, lessons_per_week=lpw,
                          duration_months=1, price=Decimal('800000'))
    student = _make_student_with_balance(f'{PREFIX}s_{tag}', balance=Decimal('1000000'))
    sub = SubscriptionService.purchase(
        student=student, tariff=tariff,
        idempotency_key=f'{PREFIX}buy_{tag}_{uuid.uuid4().hex[:6]}',
    )
    return teacher, student, sub


def monday_of(dt):
    return (dt - timedelta(days=dt.weekday())).replace(
        hour=10, minute=0, second=0, microsecond=0)


def put(booking, when, status='confirmed', slot_status='booked'):
    booking.slot.start_at = when
    booking.slot.end_at = when + timedelta(minutes=60)
    booking.slot.status = slot_status
    booking.slot.save()
    booking.status = status
    booking.save()


def free_slot(teacher, when):
    return TimeSlot.objects.create(teacher=teacher, start_at=when,
                                   end_at=when + timedelta(minutes=60), status='free')


print(f'\n{B}━━━━━━ v2 ШАГ 4: НЕДЕЛЬНАЯ КВОТА ━━━━━━{D}')
cleanup()

# ===== Q1: валидация длины паттерна =====
sect('Q1. book_schedule требует ровно lessons_per_week занятий')
teacher, student, sub = setup('q1', lpw=2)
# book_schedule нельзя вызвать (расписание уже сформировано purchase) — проверяем
# правило на отдельной чистой подписке без бронирований.
Booking.objects.filter(subscription=sub).delete()
sub.refresh_from_db()
try:
    SubscriptionService.book_schedule(sub, [{'day': 'monday', 'time': '10:00'}])  # 1 < 2
    bad = False
except ValueError as e:
    bad = True; msg = str(e)
expect(bad, 'Q1: паттерн из 1 занятия при тарифе 2/нед отклонён')
role('ученик', f'видит: «{msg if bad else ""}»')

# ===== Q2: перенос в перегруженную неделю → отказ =====
sect('Q2. Перенос в неделю с максимумом уроков → отказ')
teacher, student, sub = setup('q2', lpw=2)
books = list(Booking.objects.filter(subscription=sub).order_by('slot__start_at'))
mon = monday_of(timezone.now() + timedelta(days=14))
# Заполняем целевую неделю двумя уроками (тариф 2/нед).
put(books[0], mon + timedelta(days=0))   # пн
put(books[1], mon + timedelta(days=2))   # ср
# Третий урок — в другой неделе.
put(books[2], mon + timedelta(days=14))  # +2 недели
target = free_slot(teacher, mon + timedelta(days=4))  # пт целевой недели
role('ученик', 'в целевой неделе уже 2 урока (лимит тарифа), пытается перенести 3-й туда')
try:
    books[2].reschedule_by_student(target.id)
    over = False
except ValueError as e:
    over = True; msg2 = str(e)
expect(over, 'Q2: перенос 3-го урока в полную неделю отклонён')
role('ученик', f'видит: «{msg2 if over else ""}»')

# ===== Q3: перенос в неделю со свободным местом → успех =====
sect('Q3. Перенос в неделю со свободным местом → успех')
empty_week = monday_of(timezone.now() + timedelta(days=35))
target_ok = free_slot(teacher, empty_week + timedelta(days=1))
new_status = books[2].reschedule_by_student(target_ok.id)
books[2].refresh_from_db()
expect(new_status == 'confirmed', 'Q3: перенос в свободную неделю успешен', new_status)
expect(books[2].slot_id == target_ok.id, 'Q3: бронь на новом слоте')
role('ученик', 'успешно перенёс урок в неделю, где было место')

# ===== Итог =====
cleanup()
sect('ИТОГ')
print(f'  {G}PASSED: {len(PASSED)}{D}   {R if FAILED else G}FAILED: {len(FAILED)}{D}')
if FAILED:
    for n, d in FAILED:
        print(f'    {R}✗ {n}{D}' + (f' — {d}' if d else ''))
    sys.exit(1)
print(f'\n{G}Все сценарии Шага 4 пройдены.{D}')
