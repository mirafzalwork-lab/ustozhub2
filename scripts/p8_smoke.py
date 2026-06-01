"""Smoke-тест Phase 8 — Homework flow.

Полный сценарий:
  1. Учитель создаёт ДЗ через UI с прикреплённым PDF.
  2. Ученик видит ДЗ в /my/homework/ и в detail.
  3. Ученик сдаёт работу с текстом + файлом.
  4. Учитель видит «Ждут проверки», возвращает на доработку с комментарием.
  5. Ученик пересдаёт.
  6. Учитель оценивает 90/100.
  7. Ученик видит оценку.
  8. Валидация: чужой не может видеть ДЗ, .exe файл отклоняется.
"""
import io, os, sys, django, uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from teachers.models import (
    Booking, Subject, SubjectCategory, StudentProfile, TeacherProfile,
    TeacherSubject, TimeSlot,
)
from billing.models import (
    Homework, HomeworkAttachment, HomeworkSubmission, HomeworkSubmissionFile,
    Subscription, Tariff, Transaction, Wallet, WithdrawalRequest,
)
from billing.services import SubscriptionService, WalletService

User = get_user_model()
def green(s): return f'\033[32m{s}\033[0m'
def red(s):   return f'\033[31m{s}\033[0m'

SUFFIX = uuid.uuid4().hex[:6]
T_USER = f'p8_t_{SUFFIX}'
S_USER = f'p8_s_{SUFFIX}'
O_USER = f'p8_o_{SUFFIX}'

# Cleanup
old = User.objects.filter(username__startswith='p8_')
old_ids = list(old.values_list('pk', flat=True))
if old_ids:
    HomeworkSubmissionFile.objects.filter(submission__homework__teacher__user_id__in=old_ids).delete()
    HomeworkSubmission.objects.filter(homework__teacher__user_id__in=old_ids).delete()
    HomeworkAttachment.objects.filter(homework__teacher__user_id__in=old_ids).delete()
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

# Setup
teacher_user = User.objects.create_user(
    username=T_USER, email=f'{T_USER}@x.com', password='Pass123', user_type='teacher',
)
teacher = TeacherProfile.objects.create(
    user=teacher_user, experience_years=3, moderation_status='approved', is_active=True,
    weekly_schedule={d: [{'from': '09:00', 'to': '13:00'}] for d in
                     ('monday', 'tuesday', 'wednesday', 'thursday', 'friday')},
)
cat, _ = SubjectCategory.objects.get_or_create(name='Языки')
subject, _ = Subject.objects.get_or_create(name='Английский', defaults={'category': cat})
TeacherSubject.objects.create(teacher=teacher, subject=subject, hourly_rate=Decimal('80000'))

student = User.objects.create_user(
    username=S_USER, email=f'{S_USER}@x.com', password='Pass123', user_type='student',
)
StudentProfile.objects.create(user=student)
WalletService.credit(user=student, amount=Decimal('1000000'),
                     tx_type=Transaction.Type.DEPOSIT, idempotency_key=f'p8-seed-{SUFFIX}')

other = User.objects.create_user(
    username=O_USER, email=f'{O_USER}@x.com', password='Pass123', user_type='student',
)
StudentProfile.objects.create(user=other)

tariff = Tariff.objects.create(
    teacher=teacher, subject=subject, name='Базовый',
    lessons_per_week=2, lesson_duration_minutes=60,
    duration_months=1, price_per_month=Decimal('800000'),
)
sub = SubscriptionService.purchase(
    student=student, tariff=tariff, idempotency_key=f'p8-sub-{SUFFIX}',
)

# === Сценарий ===
tc = Client(); tc.login(username=T_USER, password='Pass123')
sc = Client(); sc.login(username=S_USER, password='Pass123')
oc = Client(); oc.login(username=O_USER, password='Pass123')

# 1. Учитель создаёт ДЗ с PDF
pdf = SimpleUploadedFile('lecture1.pdf', b'%PDF-1.4 fake pdf content', content_type='application/pdf')
r = tc.post(reverse('teacher_homework_create'), {
    'subscription': str(sub.id),
    'title': 'Прочитать главу 1',
    'description': 'Прочитать главу 1 и составить конспект на 1 страницу.',
    'due_at': '',
    'attachments': pdf,
})
print(f'[1] POST homework_create: {r.status_code} (ожидаем 302)',
      green('OK') if r.status_code == 302 else red('FAIL'))

hw = Homework.objects.filter(teacher=teacher).first()
print(f'[1a] Homework создан: {hw.title if hw else None}',
      green('OK') if hw and hw.title == 'Прочитать главу 1' else red('FAIL'))
print(f'[1b] Attachment загружен:',
      green('OK') if hw and hw.attachments.count() == 1 else red('FAIL'))

# 2. Ученик видит ДЗ в списке
r = sc.get(reverse('student_homework_list'))
html = r.content.decode('utf-8')
print(f'[2] /my/homework/ HTTP 200', green('OK') if r.status_code == 200 else red('FAIL'))
print(f'[2a] ДЗ видно в списке:',
      green('OK') if 'Прочитать главу 1' in html else red('FAIL'))

# 3. Ученик открывает detail и сдаёт работу с файлом
r = sc.get(reverse('homework_detail', args=[hw.id]))
print(f'[3] detail HTTP 200', green('OK') if r.status_code == 200 else red('FAIL'))

txt = SimpleUploadedFile('my_answer.txt', b'My answer to the homework.', content_type='text/plain')
r = sc.post(reverse('homework_detail', args=[hw.id]), {
    'text_response': 'Я прочитал главу и сделал конспект (см. файл).',
    'files': txt,
})
print(f'[4] POST submit: {r.status_code} (ожидаем 302)',
      green('OK') if r.status_code == 302 else red('FAIL'))

hw.refresh_from_db()
print(f'[4a] status=submitted', green('OK') if hw.status == 'submitted' else red(f'got {hw.status}'))
print(f'[4b] submission создан с файлом:',
      green('OK') if hasattr(hw, 'submission') and hw.submission.files.count() == 1 else red('FAIL'))

# 5. Учитель видит «Ждут проверки»
r = tc.get(reverse('teacher_homework_list'))
html = r.content.decode('utf-8')
print(f'[5] teacher list содержит pending section:',
      green('OK') if 'Ждут проверки' in html else red('FAIL'))

# 6. Учитель возвращает на доработку
r = tc.post(reverse('homework_detail', args=[hw.id]), {
    'decision': 'return',
    'feedback': 'Конспект слишком короткий, добавьте 3 примера из текста.',
})
print(f'[6] POST return: {r.status_code} (ожидаем 302)',
      green('OK') if r.status_code == 302 else red('FAIL'))
hw.refresh_from_db()
print(f'[6a] status=returned', green('OK') if hw.status == 'returned' else red(f'got {hw.status}'))
print(f'[6b] feedback сохранён',
      green('OK') if 'примера' in hw.submission.feedback else red('FAIL'))

# 7. Ученик пересдаёт
txt2 = SimpleUploadedFile('my_answer_v2.txt', b'Improved answer.', content_type='text/plain')
r = sc.post(reverse('homework_detail', args=[hw.id]), {
    'text_response': 'Доработал, добавил примеры.',
    'files': txt2,
})
print(f'[7] POST resubmit: {r.status_code} (ожидаем 302)',
      green('OK') if r.status_code == 302 else red('FAIL'))
hw.refresh_from_db()
print(f'[7a] status=submitted (после пересдачи)',
      green('OK') if hw.status == 'submitted' else red(f'got {hw.status}'))
print(f'[7b] всего 2 файла submission:',
      green('OK') if hw.submission.files.count() == 2 else red(f'got {hw.submission.files.count()}'))

# 8. Учитель оценивает 90/100
r = tc.post(reverse('homework_detail', args=[hw.id]), {
    'decision': 'grade',
    'grade': '90',
    'feedback': 'Отличная работа, теперь видно понимание материала.',
})
print(f'[8] POST grade=90: {r.status_code}', green('OK') if r.status_code == 302 else red('FAIL'))
hw.refresh_from_db()
print(f'[8a] status=graded, grade=90',
      green('OK') if hw.status == 'graded' and hw.submission.grade == 90 else red('FAIL'))

# 9. Ученик видит оценку
r = sc.get(reverse('homework_detail', args=[hw.id]))
html = r.content.decode('utf-8')
print(f'[9] Ученик видит оценку 90/100:',
      green('OK') if '90/100' in html else red('FAIL'))

# 10. Чужой не видит ДЗ
r = oc.get(reverse('homework_detail', args=[hw.id]))
print(f'[10] Чужой ученик → redirect (нет доступа):',
      green('OK') if r.status_code == 302 else red('FAIL'))

# 11. Валидация: .exe файл отклоняется
exe = SimpleUploadedFile('malware.exe', b'MZ\x90\x00fake', content_type='application/x-msdownload')
new_sub_for_test = sub  # переиспользуем
# Создадим новое ДЗ для теста exe
r = tc.post(reverse('teacher_homework_create'), {
    'subscription': str(sub.id),
    'title': 'Тест exe',
    'description': '—',
    'attachments': exe,
})
# Должен вернуть 200 (ошибка в форме), не 302
print(f'[11] .exe файл блокируется:',
      green('OK') if Homework.objects.filter(title='Тест exe').count() == 0 else red('FAIL'))

# 12. Огромный файл (> 50 MB) — fake-чек через прямое создание
huge = SimpleUploadedFile('huge.pdf', b'X' * (51 * 1024 * 1024 + 1), content_type='application/pdf')
r = tc.post(reverse('teacher_homework_create'), {
    'subscription': str(sub.id),
    'title': 'Тест huge',
    'description': '—',
    'attachments': huge,
})
print(f'[12] >50MB файл блокируется:',
      green('OK') if Homework.objects.filter(title='Тест huge').count() == 0 else red('FAIL'))

# 13. Кнопка в профиле учителя
r = tc.get('/ru/profile/')
print(f'[13] Кнопка «Домашние задания» в teacher profile:',
      green('OK') if 'Домашние задания' in r.content.decode('utf-8') else red('FAIL'))

# 14. Кнопка в профиле ученика
r = sc.get('/ru/profile/')
print(f'[14] Кнопка «Домашние задания» в student profile:',
      green('OK') if 'Домашние задания' in r.content.decode('utf-8') else red('FAIL'))

print()
print(green('✅ P8 smoke завершён.'))
