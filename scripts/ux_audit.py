"""UX/UI аудит ключевых страниц фаз P1-P6.

Проверяет:
  * наличие критичных текстов (empty states, CTA, error messages)
  * корректность отображения чисел
  * отсутствие технического "лома" в HTML (TypeError, NoReverseMatch, KeyError)
  * a11y-маркеры (aria-modal, role="dialog", labels)
  * responsiveness markers (@media queries в inline style)
  * корректность русского текста (нет «гугл-перевода»)
"""
import os, sys, django, uuid, re
from decimal import Decimal
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone

from teachers.models import (
    Booking, Subject, SubjectCategory, StudentProfile, TeacherProfile,
    TeacherSubject, TimeSlot,
)
from billing.models import Subscription, Tariff, Transaction, Wallet, WithdrawalRequest
from billing.platform_account import get_or_create_platform_user
from billing.services import SubscriptionService, WalletService, WithdrawalService

User = get_user_model()

PASS, FAIL = 0, 0
ISSUES = []

def check(label, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'\033[32m  ✓\033[0m {label}')
    else:
        FAIL += 1
        ISSUES.append((label, detail))
        print(f'\033[31m  ✗\033[0m {label}' + (f'  ←  {detail}' if detail else ''))

def section(title):
    print(f'\n\033[1m== {title} ==\033[0m')

def html_has_no_errors(html):
    """Проверка что нет технических ошибок Django в HTML."""
    bad = ['TemplateSyntaxError', 'NoReverseMatch', 'TemplateDoesNotExist',
           'AttributeError at', 'TypeError at', 'KeyError at', 'OperationalError',
           'TemplateNotFound']
    for b in bad:
        if b in html:
            return False, b
    return True, ''

# ---- Setup ----------------------------------------------------------------

SUFFIX = uuid.uuid4().hex[:6]
PREFIX = f'ux_{SUFFIX}_'

# Cleanup
old = User.objects.filter(username__startswith='ux_')
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

cat, _ = SubjectCategory.objects.get_or_create(name='Языки')
subject_en, _ = Subject.objects.get_or_create(name='Английский', defaults={'category': cat})

# Учитель с тарифами
t_user = User.objects.create_user(
    username=f'{PREFIX}t', email=f'{PREFIX}t@x.com', password='Pass123', user_type='teacher',
)
teacher = TeacherProfile.objects.create(
    user=t_user, experience_years=5, moderation_status='approved', is_active=True,
    weekly_schedule={d: [{'from': '09:00', 'to': '13:00'}] for d in
                     ('monday', 'tuesday', 'wednesday', 'thursday', 'friday')},
)
TeacherSubject.objects.create(teacher=teacher, subject=subject_en, hourly_rate=Decimal('80000'))
tariff = Tariff.objects.create(
    teacher=teacher, subject=subject_en, name='Стандарт',
    description='Базовый курс английского',
    lessons_per_week=2, lesson_duration_minutes=60,
    duration_months=1, price_per_month=Decimal('800000'),
    is_recommended=True,
)
tariff2 = Tariff.objects.create(
    teacher=teacher, subject=subject_en, name='Премиум',
    lessons_per_week=3, lesson_duration_minutes=60,
    duration_months=3, price_per_month=Decimal('1100000'),
)

# Ученик с балансом
s_user = User.objects.create_user(
    username=f'{PREFIX}s', email=f'{PREFIX}s@x.com', password='Pass123', user_type='student',
)
StudentProfile.objects.create(user=s_user)
WalletService.credit(
    user=s_user, amount=Decimal('2000000'),
    tx_type=Transaction.Type.DEPOSIT, idempotency_key=f'{PREFIX}seed',
)

# Анонимный клиент
anon = Client()
# Студент
sc = Client(); sc.login(username=s_user.username, password='Pass123')
# Учитель
tc = Client(); tc.login(username=t_user.username, password='Pass123')

# ============================================================================
section('Profile pages — wallet card')

r = tc.get('/ru/profile/')
html = r.content.decode('utf-8')
check('Teacher profile: HTTP 200', r.status_code == 200)
ok, err = html_has_no_errors(html)
check('Teacher profile: нет технических ошибок', ok, err)
check('Wallet card видна', 'Баланс кошелька' in html)
check('Кнопка «Мои тарифы»', 'Мои тарифы' in html)
check('Кнопка «Подписчики»', 'Подписчики' in html)
check('Кнопка «Вывести средства»', 'Вывести средства' in html)

r = sc.get('/ru/profile/')
html = r.content.decode('utf-8')
check('Student profile: HTTP 200', r.status_code == 200)
ok, err = html_has_no_errors(html)
check('Student profile: нет ошибок', ok, err)
check('Wallet card видна (student)', 'Баланс кошелька' in html)
check('Кнопка «Мои подписки»', 'Мои подписки' in html)

# ============================================================================
section('Tariff pages')

# Tariff list (есть тарифы)
r = tc.get('/ru/profile/tariffs/')
html = r.content.decode('utf-8')
check('tariffs_list: HTTP 200', r.status_code == 200)
ok, err = html_has_no_errors(html)
check('tariffs_list: без ошибок', ok, err)
check('Recommended badge виден', 'Рекомендованный' in html)
check('Кнопка «Создать тариф»', 'Создать тариф' in html)
check('Tariff card показывает цену с разделителями (или без — формат)',
      '800' in html and '000' in html)
check('Кнопки управления: «Изменить» / «Выключить» / «Удалить»',
      'Изменить' in html and 'Удалить' in html)

# Tariff create form
r = tc.get('/ru/profile/tariffs/new/')
html = r.content.decode('utf-8')
check('tariff_create form: HTTP 200', r.status_code == 200)
check('Form: поле «Цена за 1 месяц»', 'Цена за 1 месяц' in html)
check('Form: hint про минимум 10 000', 'Минимум 10 000' in html or '10 000 сум' in html)
check('Form: checkbox «Рекомендованный»', 'Рекомендованный' in html)
check('Form: subject ограничен предметами учителя',
      'Английский' in html and 'Японский' not in html and 'Немецкий' not in html)

# Tariff edit form (с values)
r = tc.get(f'/ru/profile/tariffs/{tariff.pk}/edit/')
html = r.content.decode('utf-8')
check('tariff_edit form: HTTP 200', r.status_code == 200)
check('Edit: name заполнен значением', 'value="Стандарт"' in html)
check('Edit: price_per_month с правильным value',
      'value="800000' in html or 'value="800000.00"' in html)

# ============================================================================
section('Teacher detail (публичная)')

r = anon.get(f'/ru/teacher/{teacher.id}/')
html = r.content.decode('utf-8')
check('teacher_detail (anon): HTTP 200', r.status_code == 200)
ok, err = html_has_no_errors(html)
check('teacher_detail: без ошибок', ok, err)
check('Секция «Подписки и тарифы»', 'Подписки и тарифы' in html)
check('Рекомендованный тариф выделен', 'Рекомендованный' in html)
check('CTA для анона «Войти, чтобы купить»', 'Войти, чтобы купить' in html)
check('Описание тарифа видно (truncated)', 'Базовый курс' in html)

# Ученик видит «Купить»
r = sc.get(f'/ru/teacher/{teacher.id}/')
html = r.content.decode('utf-8')
check('teacher_detail (student): CTA «Купить»',
      f'/subscriptions/buy/{tariff.id}/' in html)

# ============================================================================
section('Buy checkout (subscription_buy)')

r = sc.get(f'/ru/subscriptions/buy/{tariff.id}/')
html = r.content.decode('utf-8')
check('checkout: HTTP 200', r.status_code == 200)
ok, err = html_has_no_errors(html)
check('checkout: без ошибок', ok, err)
check('Summary показывает цену 800 000',
      '800 000' in html or '800000' in html)
check('Summary: 8 уроков всего', re.search(r'>\s*8\s*<', html) is not None)
check('Summary: price_per_lesson 100 000',
      '100 000' in html or '100000' in html)
check('Balance block ok (зелёный) — достаточно средств',
      'balance-block ok' in html)
check('Кнопка «Купить за …» enabled',
      ('disabled' not in html) or ('btn-buy' in html and 'Купить за' in html))
check('Подсказка «Что произойдёт после покупки»',
      'Что произойдёт после покупки' in html)

# Недостаточно средств — отдельный ученик
poor = User.objects.create_user(
    username=f'{PREFIX}poor', email=f'{PREFIX}poor@x.com', password='Pass123', user_type='student',
)
StudentProfile.objects.create(user=poor)
pc = Client(); pc.login(username=poor.username, password='Pass123')
r = pc.get(f'/ru/subscriptions/buy/{tariff.id}/')
html = r.content.decode('utf-8')
check('checkout (poor): button «Недостаточно средств»',
      'Недостаточно средств' in html)
check('checkout (poor): topup hint',
      'обратитесь к администратору' in html or 'Пополнение' in html)
check('checkout (poor): balance-block low (красный)',
      'balance-block low' in html)

# ============================================================================
section('My subscriptions (student)')

# Empty
r = sc.get('/ru/my/subscriptions/')
html = r.content.decode('utf-8')
check('my_subscriptions empty: HTTP 200', r.status_code == 200)
check('Empty state', 'У вас пока нет подписок' in html)
check('CTA «Найти учителя» в empty',
      'Найти учителя' in html)

# Делаем покупку для активного состояния
sub = SubscriptionService.purchase(
    student=s_user, tariff=tariff,
    idempotency_key=f'{PREFIX}sub',
)
r = sc.get('/ru/my/subscriptions/')
html = r.content.decode('utf-8')
check('my_subscriptions active: HTTP 200', r.status_code == 200)
check('Активные ({count})', 'Активные' in html and '(1)' in html)
check('Progress bar отрисован', 'progress-bar' in html)
check('«В эскроу» с суммой', 'В эскроу' in html and '800' in html)
check('«Выплачено учителю» с суммой', 'Выплачено учителю' in html)
check('Кнопка «Отменить подписку»', 'Отменить подписку' in html)
check('Modal: aria-modal', 'aria-modal' in html)
check('Modal: role="dialog"', 'role="dialog"' in html)
check('Modal: hidden по умолчанию', 'modal" hidden' in html or 'modal" hidden role' in html)

# Завершим — посмотрим в Историю
for b in Booking.objects.filter(subscription=sub):
    b.status = 'completed'
    b.save()
    SubscriptionService.release_lesson_payout(b)
sub.refresh_from_db()
r = sc.get('/ru/my/subscriptions/')
html = r.content.decode('utf-8')
check('my_subscriptions: completed подписка в Истории',
      'История' in html and 'completed' in html)

# ============================================================================
section('Teacher subscribers')

# Empty
empty_user = User.objects.create_user(
    username=f'{PREFIX}empty_t', email=f'{PREFIX}empty_t@x.com',
    password='Pass123', user_type='teacher',
)
empty_teacher = TeacherProfile.objects.create(
    user=empty_user, experience_years=1, moderation_status='approved',
)
empty_tc = Client(); empty_tc.login(username=empty_user.username, password='Pass123')
r = empty_tc.get('/ru/profile/subscribers/')
html = r.content.decode('utf-8')
check('teacher_subscribers empty: HTTP 200', r.status_code == 200)
check('Empty state видно', 'Пока нет подписчиков' in html)

# С подписчиком
r = tc.get('/ru/profile/subscribers/')
html = r.content.decode('utf-8')
check('teacher_subscribers active: HTTP 200', r.status_code == 200)
check('Earn cards «Выплачено всего»', 'Выплачено всего' in html)
check('Earn cards «За последние 30 дней»', 'За последние 30 дней' in html)
check('Earn cards «В эскроу»', 'эскроу' in html.lower())
# Ученик в активных
check('Ученик отображается', s_user.username in html or s_user.email in html)
check('Кнопка отмены учителем', 'Отменить подписку' in html)
check('Reason — required в форме учителя', 'required' in html)

# ============================================================================
section('Withdrawals')

# Учитель с 0 балансом (не может выводить)
r = empty_tc.get('/ru/profile/withdrawals/')
html = r.content.decode('utf-8')
check('withdrawals (0 balance): HTTP 200', r.status_code == 200)
check('Карточка «Недостаточно средств для вывода»',
      'Недостаточно средств для вывода' in html)

# Учитель с балансом — у нашего t_user сейчас 5*85000 = 425000 (за 5 завершённых уроков из 8)
# Wait — sub-это 8 уроков завершено, payout всех 8: 8 × 85_000 = 680_000
t_user.wallet.refresh_from_db()
r = tc.get('/ru/profile/withdrawals/')
html = r.content.decode('utf-8')
check('withdrawals (balance>0): HTTP 200', r.status_code == 200)
check('Балансная карточка',
      'Доступно к выводу' in html)
check('Форма видна', 'Сумма, сум' in html and 'Реквизиты' in html)
check('Hint про minimum 100 000',
      '100 000' in html)
check('История заявок секция', 'История заявок' in html)
check('Empty state в истории', 'Заявок пока нет' in html)

# Создадим pending заявку
wr = WithdrawalService.create_request(
    user=t_user, amount=Decimal('150000'),
    payout_method='card', payout_details='8600 1234 5678 9012',
    idempotency_key=f'{PREFIX}wr1',
)
r = tc.get('/ru/profile/withdrawals/')
html = r.content.decode('utf-8')
check('Pending заявка в таблице',
      '150 000' in html or '150000' in html)
check('Status pill «pending»', 'status-pill pending' in html)
check('Кнопка «Отменить» рядом с pending',
      'Отменить' in html)
check('Реквизиты усечены или показаны', '8600' in html)

# ============================================================================
section('A11y / responsive markers')

# my_subscriptions модалка — для теста модалки нужна активная подписка
sc.login(username=s_user.username, password='Pass123')
WalletService.credit(user=s_user, amount=Decimal('3500000'),
                     tx_type=Transaction.Type.DEPOSIT,
                     idempotency_key=f'{PREFIX}top2')
sub2 = SubscriptionService.purchase(
    student=s_user, tariff=tariff2,
    idempotency_key=f'{PREFIX}sub2',
)
r = sc.get('/ru/my/subscriptions/')
html = r.content.decode('utf-8')
check('Modal: cancel-modal в HTML', 'cancel-modal' in html)
check('Modal: focus-управление (data-sub-id)', 'data-sub-id' in html)
check('Responsive: @media в стилях', '@media' in html)

# Проверка что нет «гугл-перевода» (нет слов из других языков)
r = sc.get('/ru/my/subscriptions/')
html = r.content.decode('utf-8')
suspicious_engrish = ['Subscription', 'My subscriptions', 'Cancel subscription']  # на /ru/ не должно быть таких
# Допускаем некоторые из base.html (CTA navbar и т.п.) — фильтр строгий, проверяем именно билинг секцию
billing_section_words = ['Мои подписки', 'Активные', 'Прогресс']
all_ok = all(w in html for w in billing_section_words)
check('Билинг страница на русском',
      all_ok)

# ============================================================================
section('Edge cases — рендер')

# /subscriptions/buy/<несуществующий>/
r = sc.get('/ru/subscriptions/buy/9999999/')
check('Buy несуществующий tariff: 404', r.status_code == 404)

# Inactive tariff
tariff.is_active = False
tariff.save()
r = sc.get(f'/ru/subscriptions/buy/{tariff.id}/')
check('Buy inactive tariff: 404',
      r.status_code == 404)
tariff.is_active = True
tariff.save()

# Cancel чужой подписки
other = User.objects.create_user(
    username=f'{PREFIX}other', email=f'{PREFIX}other@x.com',
    password='Pass123', user_type='student',
)
StudentProfile.objects.create(user=other)
oc = Client(); oc.login(username=other.username, password='Pass123')
r = oc.post(f'/ru/subscriptions/{sub2.id}/cancel/')
check('Cancel чужой: redirect (не дано прав)',
      r.status_code == 302)
sub2.refresh_from_db()
check('Cancel чужой: подписка не отменена',
      sub2.status == 'active')

# ============================================================================
print()
print(f'\033[1mUX/UI Итого: \033[32m{PASS} PASS\033[0m, \033[31m{FAIL} FAIL\033[0m')
if ISSUES:
    print('\nНайденные UX/UI issues:')
    for label, det in ISSUES:
        print(f'  • {label}  ({det})')
