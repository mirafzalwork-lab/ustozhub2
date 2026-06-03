"""Smoke-тест v2 Шаг 7 — анти-обход платформы.

Сценарии:
  7а. Маскировка контактов в чате:
      M1. телефон / @хендл / ссылка / t.me вырезаются;
      M2. чистый текст не трогается;
      M3. под порогом оплаченных уроков → маскируем;
      M4. после порога → контакты разрешены.
  7б. Запрет внешних видеоссылок:
      L1. внешний Zoom-URL отклонён;
      L2. пустая ссылка ОК (подставится Jitsi);
      L3. наш Jitsi-URL разрешён.

Запуск: python scripts/v2_step7_smoke.py
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

from teachers.models import Booking, Conversation, TimeSlot  # noqa: E402
from billing.models import Subscription, Transaction, Wallet  # noqa: E402
from teachers.contact_filter import mask_contacts, apply_contact_policy  # noqa: E402
from teachers.booking_views import _validate_meeting_url  # noqa: E402
from billing.tests import (  # noqa: E402
    _make_teacher_with_subject, _make_student_with_balance,
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


PREFIX = 'v2s7_'
def cleanup():
    ids = list(User.objects.filter(username__startswith=PREFIX).values_list('pk', flat=True))
    if not ids:
        return
    Booking.objects.filter(student_id__in=ids).delete()
    Booking.objects.filter(slot__teacher__user_id__in=ids).delete()
    TimeSlot.objects.filter(teacher__user_id__in=ids).delete()
    Conversation.objects.filter(student_id__in=ids).delete()
    Subscription.objects.filter(student_id__in=ids).delete()
    Transaction.objects.filter(wallet__user_id__in=ids).delete()
    from teachers.models import TeacherSubject, TeacherProfile, StudentProfile
    TeacherSubject.objects.filter(teacher__user_id__in=ids).delete()
    TeacherProfile.objects.filter(user_id__in=ids).delete()
    StudentProfile.objects.filter(user_id__in=ids).delete()
    Wallet.objects.filter(user_id__in=ids).delete()
    User.objects.filter(pk__in=ids).delete()


print(f'\n{B}━━━━━━ v2 ШАГ 7: АНТИ-ОБХОД ━━━━━━{D}')
print(f'  (CONTACT_MASK_MIN_PAID_LESSONS={settings.CONTACT_MASK_MIN_PAID_LESSONS}, '
      f'ALLOW_EXTERNAL_MEETING_URLS={settings.ALLOW_EXTERNAL_MEETING_URLS})')
cleanup()

# ===== 7а M1: вырезание контактов =====
sect('7а·M1. Телефон / @хендл / ссылка вырезаются')
cases = [
    ('Звони +998 90 123 45 67', 'телефон'),
    ('мой телеграм @ustoz_repetitor пиши', '@хендл'),
    ('вот ссылка https://zoom.us/j/123456 заходи', 'ссылка'),
    ('t.me/teacher123', 't.me'),
    ('номер 901234567 запиши', 'голый телефон'),
]
for text, label in cases:
    masked, was = mask_contacts(text)
    expect(was and 'контакт скрыт' in masked, f'7а·M1: {label} замаскирован', masked)

# ===== 7а M2: чистый текст не трогаем =====
sect('7а·M2. Обычное сообщение не меняется')
clean = 'Здравствуйте! Давайте начнём урок в понедельник, повторим времена.'
masked, was = mask_contacts(clean)
expect(not was and masked == clean, '7а·M2: чистый текст без изменений')
# Короткие числа (год, номер урока) не считаются телефоном
masked2, was2 = mask_contacts('Урок 3, глава 12, 2025 год')
expect(not was2, '7а·M2: короткие числа не маскируются', masked2)

# ===== 7а M3/M4: порог по оплаченным урокам =====
sect('7а·M3/M4. Порог доверия: до — маскируем, после — разрешаем')
teacher, subject = _make_teacher_with_subject(f'{PREFIX}t_a')
student = _make_student_with_balance(f'{PREFIX}s_a', balance=Decimal('0'))
conv = Conversation.objects.create(teacher=teacher, student=student, subject=subject)
text = 'мой номер +998901234567'
out, was = apply_contact_policy(conv, text)
expect(was and 'контакт скрыт' in out, '7а·M3: до порога — контакт замаскирован')
role('ученик', 'пишет телефон новому учителю → платформа скрывает')

# Добавляем оплаченные уроки до порога.
thr = settings.CONTACT_MASK_MIN_PAID_LESSONS
for i in range(thr):
    start = timezone.now() - timedelta(days=i + 1, hours=2)
    slot = TimeSlot.objects.create(teacher=teacher, start_at=start,
                                   end_at=start + timedelta(hours=1), status='booked')
    Booking.objects.create(slot=slot, student=student, subject=subject,
                           status='completed', is_trial=False)
out2, was2 = apply_contact_policy(conv, text)
expect(not was2 and out2 == text, f'7а·M4: после {thr} оплаченных уроков — контакты разрешены', out2)
role('учитель', f'после {thr} оплаченных уроков доверие установлено → обмен контактами открыт')

# ===== 7б: запрет внешних видеоссылок =====
sect('7б·L1. Внешний Zoom-URL отклонён')
ok, err = _validate_meeting_url('https://zoom.us/j/9999999')
expect(not ok, '7б·L1: Zoom-ссылка отклонена', err[:50])
role('учитель', 'пытается вписать Zoom → запрещено, только видеокомната платформы')

sect('7б·L2. Пустая ссылка разрешена (подставится Jitsi)')
ok2, err2 = _validate_meeting_url('')
expect(ok2, '7б·L2: пустая ссылка ОК')

sect('7б·L3. Наш Jitsi-URL разрешён')
ok3, err3 = _validate_meeting_url(f'{settings.JITSI_BASE_URL}/booking-abc123')
expect(ok3, '7б·L3: Jitsi-ссылка разрешена', err3[:50] if not ok3 else '')

# ===== Итог =====
cleanup()
sect('ИТОГ')
print(f'  {G}PASSED: {len(PASSED)}{D}   {R if FAILED else G}FAILED: {len(FAILED)}{D}')
if FAILED:
    for n, d in FAILED:
        print(f'    {R}✗ {n}{D}' + (f' — {d}' if d else ''))
    sys.exit(1)
print(f'\n{G}Все сценарии Шага 7 пройдены.{D}')
