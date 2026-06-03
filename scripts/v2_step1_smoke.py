"""Smoke-тест v2 Шаг 1 — escrow-таймаут истёкших подписок.

Сценарии (каждый проверяется глазами УЧЕНИКА и УЧИТЕЛЯ):
  A. Полностью непроведённая истёкшая подписка → полный возврат ученику.
  B. Часть уроков completed-но-не-paid → выплата учителю + возврат остатка.
  C. Идемпотентность: повторный settle_expired → None, деньги не двоятся.
  D. Ещё не истёкшая подписка → settle_expired ничего не делает (None).
  E. Денежный инвариант: Σ(wallet) + Σ(escrow активных) сохраняется.

Запуск: python scripts/v2_step1_smoke.py
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
    """Σ всех кошельков + escrow активных подписок (инвариант системы)."""
    w = Wallet.objects.aggregate(s=Sum('balance'))['s'] or Decimal('0')
    e = Subscription.objects.filter(
        status__in=Subscription.ACTIVE_STATUSES
    ).aggregate(s=Sum('escrow_balance'))['s'] or Decimal('0')
    return w + e


PREFIX = 'v2s1_'
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
    Wallet.objects.filter(user_id__in=ids).delete()  # PROTECT → удаляем до User
    User.objects.filter(pk__in=ids).delete()


def make_setup(tag, price=Decimal('800000'), lpw=2):
    teacher, subject = _make_teacher_with_subject(f'{PREFIX}t_{tag}')
    tariff = _make_tariff(teacher, subject, lessons_per_week=lpw,
                          duration_months=1, price=price)
    student = _make_student_with_balance(f'{PREFIX}s_{tag}', balance=Decimal('1000000'))
    sub = SubscriptionService.purchase(
        student=student, tariff=tariff, idempotency_key=f'{PREFIX}buy_{tag}_{uuid.uuid4().hex[:6]}',
    )
    return teacher, student, sub


def expire_now(sub):
    """Сдвигаем срок подписки в прошлое за пределы grace-окна."""
    grace = settings.PAYOUT_GRACE_HOURS
    sub.expires_at = timezone.now() - timedelta(hours=grace + 1)
    sub.save(update_fields=['expires_at'])


print(f'\n{B}━━━━━━ v2 ШАГ 1: ESCROW-ТАЙМАУТ ━━━━━━{D}')
cleanup()

# ============ Сценарий A: полностью непроведённая истёкшая подписка ============
sect('A. Истёкшая подписка без проведённых уроков → полный возврат')
teacher, student, sub = make_setup('A')
student.wallet.refresh_from_db()
role('ученик', f'купил подписку: баланс {student.wallet.balance}, escrow {sub.escrow_balance}')
n_book = Booking.objects.filter(subscription=sub).count()
role('учитель', f'в расписании {n_book} будущих уроков (статусы confirmed)')
expect(sub.escrow_balance == Decimal('800000.00'), 'A: escrow=800k после покупки')
expect(student.wallet.balance == Decimal('200000.00'), 'A: баланс ученика 200k после покупки')

expire_now(sub)
role('система', 'срок подписки истёк (за пределами grace)')
money_A = total_money()
result = SubscriptionService.settle_expired(sub)
sub.refresh_from_db(); student.wallet.refresh_from_db(); teacher.user.wallet.refresh_from_db()

expect(result is not None, 'A: settle_expired сработал')
expect(total_money() == money_A, 'A: денежный инвариант вокруг settle сохранён',
       f'{money_A} → {total_money()}')
expect(result['refunded'] == Decimal('800000.00'), 'A: возвращено 800k', str(result['refunded']))
expect(result['paid_out'] == 0, 'A: учителю не выплачено (уроков не было)')
expect(sub.status == Subscription.Status.EXPIRED, 'A: статус EXPIRED', sub.status)
expect(sub.escrow_balance == Decimal('0.00'), 'A: escrow обнулён')
expect(student.wallet.balance == Decimal('1000000.00'), 'A: ученику вернули всё (1 000 000)',
       str(student.wallet.balance))
expect(teacher.user.wallet.balance == Decimal('0.00'), 'A: учитель ничего не получил')
exp_books = Booking.objects.filter(subscription=sub, status='expired').count()
expect(exp_books == n_book, f'A: все {n_book} броней → expired', str(exp_books))
free_slots = TimeSlot.objects.filter(teacher=teacher, status='free').count()
expect(free_slots >= n_book, 'A: слоты освобождены', str(free_slots))
role('ученик', f'видит: подписка завершена, баланс {student.wallet.balance} (возврат пришёл)')
role('учитель', 'видит: будущие уроки сняты, слоты снова свободны')

# ============ Сценарий B: часть уроков проведена (completed, не выплачено) ============
sect('B. Истёкшая подписка с 3 проведёнными уроками → выплата + возврат остатка')
teacher, student, sub = make_setup('B')
bookings = list(Booking.objects.filter(subscription=sub).order_by('slot__start_at'))
for b in bookings[:3]:
    b.slot.start_at = timezone.now() - timedelta(days=2, hours=1)
    b.slot.end_at = timezone.now() - timedelta(days=2)
    b.slot.status = 'booked'
    b.slot.save()
    b.status = 'completed'
    b.save()
role('учитель', 'провёл 3 урока (completed), выплат ещё не было')
role('ученик', f'escrow до таймаута: {sub.escrow_balance}')

expire_now(sub)
money_B = total_money()
result = SubscriptionService.settle_expired(sub)
sub.refresh_from_db(); student.wallet.refresh_from_db(); teacher.user.wallet.refresh_from_db()
platform = get_or_create_platform_user(); platform.wallet.refresh_from_db()

expect(total_money() == money_B, 'B: денежный инвариант вокруг settle сохранён',
       f'{money_B} → {total_money()}')
expect(result['paid_out'] == 3, 'B: выплачено 3 урока', str(result['paid_out']))
expect(teacher.user.wallet.balance == Decimal('255000.00'),
       'B: учителю 3×85k=255k', str(teacher.user.wallet.balance))
expect(result['refunded'] == Decimal('500000.00'),
       'B: ученику возврат 5×100k=500k', str(result['refunded']))
expect(student.wallet.balance == Decimal('700000.00'),
       'B: баланс ученика 200k+500k=700k', str(student.wallet.balance))
expect(sub.status == Subscription.Status.EXPIRED, 'B: статус EXPIRED (3<8 уроков)', sub.status)
role('учитель', f'видит: заработок {teacher.user.wallet.balance} за 3 проведённых урока')
role('ученик', f'видит: возврат {result["refunded"]} за 5 непроведённых уроков')

# ============ Сценарий C: идемпотентность ============
sect('C. Повторный settle_expired → None, деньги не двоятся')
bal_before = student.wallet.balance
result2 = SubscriptionService.settle_expired(sub)
student.wallet.refresh_from_db()
expect(result2 is None, 'C: повторный вызов вернул None')
expect(student.wallet.balance == bal_before, 'C: баланс ученика не изменился')

# ============ Сценарий D: не истёкшая подписка не трогается ============
sect('D. Активная (не истёкшая) подписка → settle_expired = None')
teacher, student, sub = make_setup('D')
result3 = SubscriptionService.settle_expired(sub)  # expires_at в будущем
sub.refresh_from_db()
expect(result3 is None, 'D: settle_expired вернул None')
expect(sub.status == Subscription.Status.ACTIVE, 'D: статус остался ACTIVE', sub.status)
expect(sub.escrow_balance == Decimal('800000.00'), 'D: escrow не тронут')

# ============ Итог ============
cleanup()
sect('ИТОГ')
print(f'  {G}PASSED: {len(PASSED)}{D}   {R if FAILED else G}FAILED: {len(FAILED)}{D}')
if FAILED:
    for n, d in FAILED:
        print(f'    {R}✗ {n}{D}' + (f' — {d}' if d else ''))
    sys.exit(1)
print(f'\n{G}Все сценарии Шага 1 пройдены.{D}')
