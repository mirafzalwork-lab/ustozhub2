"""Smoke-тест admin-страниц финансов.

Проверка реального admin-flow:
  1. Admin открывает hub → видит метрики
  2. Admin ищет пользователя → видит баланс + историю
  3. Admin пополняет → транзакция в ledger, balance изменён
  4. Admin списывает → ditto
  5. Admin видит pending withdrawals + одобряет
  6. Admin отменяет подписку с refund
"""
import os, sys, django, uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model
from django.test import Client

from teachers.models import (
    Booking, Subject, SubjectCategory, StudentProfile, TeacherProfile,
    TeacherSubject, TimeSlot,
)
from billing.models import (
    Homework, HomeworkAttachment, HomeworkSubmission, HomeworkSubmissionFile,
    Subscription, Tariff, Transaction, Wallet, WithdrawalRequest,
)
from billing.services import SubscriptionService, WalletService, WithdrawalService

User = get_user_model()
def green(s): return f'\033[32m{s}\033[0m'
def red(s):   return f'\033[31m{s}\033[0m'

SUFFIX = uuid.uuid4().hex[:6]
A_USER = f'qa_a_{SUFFIX}'
T_USER = f'qa_t_{SUFFIX}'
S_USER = f'qa_s_{SUFFIX}'

# Cleanup
old = User.objects.filter(username__startswith='qa_a_').union(
    User.objects.filter(username__startswith='qa_t_'),
    User.objects.filter(username__startswith='qa_s_'),
)
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
    User.objects.filter(pk__in=old_ids).delete()

# Setup
admin_user = User.objects.create_user(
    username=A_USER, email=f'{A_USER}@x.com', password='Pass123',
    is_staff=True, is_superuser=True,
)
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

# Admin client
ac = Client()
ac.login(username=A_USER, password='Pass123')

# 1. Hub
r = ac.get('/ru/admin-dashboard/billing/')
print(f'[1] GET billing hub: {r.status_code}', green('OK') if r.status_code == 200 else red('FAIL'))
html = r.content.decode('utf-8')
print(f'[1a] Видны метрики «Доход платформы»:',
      green('OK') if 'Доход платформы' in html else red('FAIL'))
print(f'[1b] Быстрые действия видны:',
      green('OK') if 'Пополнить баланс пользователю' in html else red('FAIL'))

# 2. Wallet search — пустой
r = ac.get('/ru/admin-dashboard/billing/wallets/')
print(f'[2] GET wallets без q: {r.status_code}', green('OK') if r.status_code == 200 else red('FAIL'))

# 3. Search по student
r = ac.get(f'/ru/admin-dashboard/billing/wallets/?q={S_USER[:6]}')
html = r.content.decode('utf-8')
print(f'[3] Search по S_USER: HTTP {r.status_code}', green('OK') if r.status_code == 200 else red('FAIL'))
# Один результат → автооткрытие
print(f'[3a] Wallet card открыт автоматически (один результат):',
      green('OK') if 'Пополнить / Списать' in html else red('FAIL'))

# 4. Top-up student
r = ac.post(f'/ru/admin-dashboard/billing/wallets/{student.id}/topup/', {
    'operation': 'credit',
    'amount': '1500000',
    'reason': 'тестовое пополнение',
})
print(f'[4] POST top-up: {r.status_code} (302)', green('OK') if r.status_code == 302 else red('FAIL'))

student.wallet.refresh_from_db()
print(f'[4a] balance = 1 500 000:',
      green('OK') if student.wallet.balance == Decimal('1500000') else red(f'got {student.wallet.balance}'))

# 5. Debit student
r = ac.post(f'/ru/admin-dashboard/billing/wallets/{student.id}/topup/', {
    'operation': 'debit',
    'amount': '300000',
    'reason': 'корректировка',
})
student.wallet.refresh_from_db()
print(f'[5] POST debit 300k → balance = 1 200 000:',
      green('OK') if student.wallet.balance == Decimal('1200000') else red(f'got {student.wallet.balance}'))

# 6. Subscriptions list
# Создадим подписку чтобы было что показывать
tariff = Tariff.objects.create(
    teacher=teacher, subject=subject, name='Тест',
    lessons_per_week=2, lesson_duration_minutes=60,
    duration_months=1, price_per_month=Decimal('800000'),
)
sub = SubscriptionService.purchase(
    student=student, tariff=tariff, idempotency_key=f'admin-smoke-{SUFFIX}',
)
r = ac.get('/ru/admin-dashboard/billing/subscriptions/')
html = r.content.decode('utf-8')
print(f'[6] GET subscriptions: {r.status_code}', green('OK') if r.status_code == 200 else red('FAIL'))
print(f'[6a] подписка видна:',
      green('OK') if S_USER in html and 'Английский' in html else red('FAIL'))

# 7. Cancel by admin
r = ac.post(f'/ru/admin-dashboard/billing/subscriptions/{sub.id}/cancel/', {
    'reason': 'тест админ-отмены',
})
sub.refresh_from_db()
print(f'[7] Admin cancel: status={sub.status}',
      green('OK') if sub.status == 'cancelled_by_admin' else red(f'got {sub.status}'))
student.wallet.refresh_from_db()
print(f'[7a] Refund прошёл (баланс ~2M):',
      green('OK') if student.wallet.balance == Decimal('2000000') else red(f'got {student.wallet.balance}'))

# 8. Withdrawals — создадим pending заявку как учитель и одобрим
WalletService.credit(user=teacher_user, amount=Decimal('500000'),
                     tx_type=Transaction.Type.DEPOSIT, idempotency_key=f'qa-t-seed-{SUFFIX}')
wr = WithdrawalService.create_request(
    user=teacher_user, amount=Decimal('200000'),
    payout_method='card', payout_details='8600 1234',
    idempotency_key=f'qa-wr-{SUFFIX}',
)

r = ac.get('/ru/admin-dashboard/billing/withdrawals/')
html = r.content.decode('utf-8')
print(f'[8] GET withdrawals pending: {r.status_code}', green('OK') if r.status_code == 200 else red('FAIL'))
print(f'[8a] заявка видна в списке:',
      green('OK') if T_USER in html else red('FAIL'))

# 9. Approve
r = ac.post(f'/ru/admin-dashboard/billing/withdrawals/{wr.id}/action/', {
    'action': 'approve', 'note': 'проверено',
})
wr.refresh_from_db()
print(f'[9] Admin approve: status={wr.status}',
      green('OK') if wr.status == 'approved' else red(f'got {wr.status}'))

# 10. Complete
r = ac.post(f'/ru/admin-dashboard/billing/withdrawals/{wr.id}/action/', {
    'action': 'complete', 'note': 'перевод 25.05',
})
wr.refresh_from_db()
print(f'[10] Admin complete: status={wr.status}',
      green('OK') if wr.status == 'completed' else red(f'got {wr.status}'))

# 11. Не-staff не может зайти
sc = Client(); sc.login(username=S_USER, password='Pass123')
r = sc.get('/ru/admin-dashboard/billing/')
print(f'[11] Student → admin billing: redirect',
      green('OK') if r.status_code == 302 else red(f'got {r.status_code}'))

# 12. Главная админка содержит секцию «Финансы»
r = ac.get('/ru/admin-dashboard/')
html = r.content.decode('utf-8')
print(f'[12] /admin-dashboard/ содержит секцию «Финансы»:',
      green('OK') if 'Финансы платформы' in html else red('FAIL'))

# 13. Sidebar содержит ссылки на billing
print(f'[13] Sidebar содержит «Кошельки» / «Выводы»:',
      green('OK') if 'Кошельки' in html and 'Выводы' in html else red('FAIL'))

print()
print(green('✅ Admin smoke завершён.'))
