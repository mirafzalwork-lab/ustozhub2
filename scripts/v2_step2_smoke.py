"""Smoke-тест v2 Шаг 2 — семантика неявок (no_show_student).

Сценарии (глазами УЧЕНИКА и УЧИТЕЛЯ):
  S1. Оба зашли → completed.
  S2. Учитель зашёл, ученик нет → no_show_student (урок засчитан учителю).
  S3. Учитель не зашёл → no_show_teacher (возврат ученику, выплаты нет).
  S4. Внешняя ссылка (не Jitsi) → completed по времени (учителя не штрафуем).
  S5. no_show_student уменьшает остаток пакета (completed_lessons++).
  S6. Выплата за no_show_student: учителю 85%, платформе 15%, escrow -100k.
  S7. Денежный инвариант вокруг выплаты сохранён.

Запуск: python scripts/v2_step2_smoke.py
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
from billing.services import PayoutError, SubscriptionService  # noqa: E402
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


PREFIX = 'v2s2_'
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


def past_lesson(sub, idx=0, *, jitsi=True, teacher_in=False, student_in=False):
    """Готовит idx-й урок подписки: в прошлом, с нужным присутствием."""
    b = list(Booking.objects.filter(subscription=sub).order_by('slot__start_at'))[idx]
    b.slot.start_at = timezone.now() - timedelta(hours=3)
    b.slot.end_at = timezone.now() - timedelta(hours=2)
    b.slot.status = 'booked'
    b.slot.save()
    b.meeting_url = f'{settings.JITSI_BASE_URL}/room-{b.id}' if jitsi else 'https://zoom.us/j/123'
    b.teacher_joined_at = timezone.now() - timedelta(hours=2, minutes=55) if teacher_in else None
    b.student_joined_at = timezone.now() - timedelta(hours=2, minutes=54) if student_in else None
    b.save()
    return b


print(f'\n{B}━━━━━━ v2 ШАГ 2: СЕМАНТИКА НЕЯВОК ━━━━━━{D}')
cleanup()

# ===== S1: оба зашли → completed =====
sect('S1. Оба участника зашли → completed')
teacher, student, sub = make_setup('s1')
b = past_lesson(sub, 0, jitsi=True, teacher_in=True, student_in=True)
res = b.settle_after_end()
expect(res == 'completed', 'S1: статус completed', res)
role('учитель', 'провёл урок, оба были онлайн → урок завершён')

# ===== S2: учитель да, ученик нет → no_show_student =====
sect('S2. Учитель зашёл, ученик нет → no_show_student')
teacher, student, sub = make_setup('s2')
b = past_lesson(sub, 0, jitsi=True, teacher_in=True, student_in=False)
res = b.settle_after_end()
b.refresh_from_db()
expect(res == 'no_show_student', 'S2: статус no_show_student', res)
role('учитель', 'пришёл и ждал, ученик не подключился → урок засчитан учителю')
role('ученик', 'не пришёл → урок списан из пакета, возврата нет')

# ===== S3: учитель не зашёл → no_show_teacher =====
sect('S3. Учитель не зашёл → no_show_teacher')
teacher, student, sub = make_setup('s3')
b = past_lesson(sub, 0, jitsi=True, teacher_in=False, student_in=True)
res = b.settle_after_end()
expect(res == 'no_show_teacher', 'S3: статус no_show_teacher', res)
# Выплата за no_show_teacher невозможна
b.refresh_from_db()
try:
    SubscriptionService.release_lesson_payout(b)
    paid = True
except PayoutError:
    paid = False
expect(not paid, 'S3: выплата за no_show_teacher отклонена (PayoutError)')
role('учитель', 'не вышел на урок → не оплачивается')
role('ученик', 'учитель не пришёл → урок подлежит возврату (Celery)')

# ===== S4: внешняя ссылка → completed по времени =====
sect('S4. Внешняя ссылка (Zoom), присутствие не отслеживается → completed')
teacher, student, sub = make_setup('s4')
b = past_lesson(sub, 0, jitsi=False, teacher_in=False, student_in=False)
res = b.settle_after_end()
expect(res == 'completed', 'S4: completed (учителя не штрафуем без данных)', res)

# ===== S5 + S6 + S7: no_show_student уменьшает пакет и оплачивается =====
sect('S5–S7. no_show_student: прогресс пакета + выплата учителю + инвариант')
teacher, student, sub = make_setup('s567')
sub.refresh_from_db()
completed_before = sub.completed_lessons
b = past_lesson(sub, 0, jitsi=True, teacher_in=True, student_in=False)
res = b.settle_after_end()
sub.refresh_from_db()
expect(res == 'no_show_student', 'S5: урок → no_show_student')
expect(sub.completed_lessons == completed_before + 1,
       'S5: completed_lessons +1 (урок потреблён из пакета)',
       f'{completed_before}→{sub.completed_lessons}')

money_before = total_money()
teacher.user.wallet.refresh_from_db()
t_before = teacher.user.wallet.balance
platform = get_or_create_platform_user(); platform.wallet.refresh_from_db()
p_before = platform.wallet.balance
escrow_before = sub.escrow_balance

b.refresh_from_db()
ok = SubscriptionService.release_lesson_payout(b)
sub.refresh_from_db(); teacher.user.wallet.refresh_from_db(); platform.wallet.refresh_from_db()

expect(ok is True, 'S6: выплата за no_show_student прошла')
expect(teacher.user.wallet.balance - t_before == Decimal('85000.00'),
       'S6: учителю 85k', str(teacher.user.wallet.balance - t_before))
expect(platform.wallet.balance - p_before == Decimal('15000.00'),
       'S6: платформе 15k', str(platform.wallet.balance - p_before))
expect(escrow_before - sub.escrow_balance == Decimal('100000.00'),
       'S6: escrow -100k', str(escrow_before - sub.escrow_balance))
expect(total_money() == money_before,
       'S7: денежный инвариант вокруг выплаты сохранён',
       f'{money_before} → {total_money()}')
role('учитель', f'получил 85 000 за урок, на который ученик не пришёл')

# ===== Итог =====
cleanup()
sect('ИТОГ')
print(f'  {G}PASSED: {len(PASSED)}{D}   {R if FAILED else G}FAILED: {len(FAILED)}{D}')
if FAILED:
    for n, d in FAILED:
        print(f'    {R}✗ {n}{D}' + (f' — {d}' if d else ''))
    sys.exit(1)
print(f'\n{G}Все сценарии Шага 2 пройдены.{D}')
