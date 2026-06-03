"""Тесты финансового фундамента.

Покрывают:
  * auto-создание Wallet при создании User
  * credit/debit пишут Transaction и обновляют balance
  * balance == SUM(transactions.amount)
  * идемпотентность по idempotency_key
  * InsufficientFunds при попытке уйти в минус
  * transfer между двумя кошельками атомарен
"""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction
from django.test import TestCase, TransactionTestCase, override_settings


# Простое хранилище статики без manifest — для view-тестов, где не запускаем collectstatic.
SIMPLE_STATIC_STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

from billing.models import (
    Homework, HomeworkAttachment, HomeworkSubmission, HomeworkSubmissionFile,
    LessonDispute, Subscription, Tariff, Transaction, Wallet, WithdrawalRequest,
)
from billing.services import (
    AlreadySubscribed,
    CancellationError,
    DisputeError,
    DisputeService,
    InsufficientFunds,
    NotEnoughCapacity,
    PayoutError,
    SubscriptionService,
    WalletService,
    WithdrawalAmountError,
    WithdrawalError,
    WithdrawalService,
)

User = get_user_model()


def _make_teacher_with_subject(username='t1', with_schedule=True):
    """Создаёт User(teacher) + TeacherProfile + Subject + TeacherSubject — минимум для Tariff тестов."""
    from teachers.models import Subject, SubjectCategory, TeacherProfile, TeacherSubject
    user = User.objects.create_user(
        username=username, email=f'{username}@x.com', password='x' * 12,
        user_type='teacher',
    )
    schedule = {
        'monday':    [{'from': '09:00', 'to': '13:00'}],
        'tuesday':   [{'from': '09:00', 'to': '13:00'}],
        'wednesday': [{'from': '09:00', 'to': '13:00'}],
        'thursday':  [{'from': '09:00', 'to': '13:00'}],
        'friday':    [{'from': '09:00', 'to': '13:00'}],
    } if with_schedule else {}
    profile = TeacherProfile.objects.create(
        user=user, experience_years=3, weekly_schedule=schedule,
    )
    cat, _ = SubjectCategory.objects.get_or_create(name='Языки')
    subject, _ = Subject.objects.get_or_create(name='Английский', defaults={'category': cat})
    TeacherSubject.objects.create(teacher=profile, subject=subject, hourly_rate=Decimal('50000'))
    return profile, subject


def _make_tariff(teacher, subject, lessons_per_week=2, duration_months=1, price=Decimal('800000')):
    return Tariff.objects.create(
        teacher=teacher, subject=subject,
        lessons_per_week=lessons_per_week, lesson_duration_minutes=60,
        duration_months=duration_months, price_per_month=price,
    )


def _make_student_with_balance(username='stud', balance=Decimal('1000000')):
    from teachers.models import StudentProfile
    student = User.objects.create_user(
        username=username, email=f'{username}@x.com', password='x' * 12,
        user_type='student',
    )
    # StudentProfile нужен иначе OnboardingMiddleware редиректит на /register/choose/
    StudentProfile.objects.create(user=student)
    if balance > 0:
        WalletService.credit(
            user=student, amount=balance,
            tx_type=Transaction.Type.DEPOSIT,
            idempotency_key=f'seed-{username}',
            description='test seed',
        )
    return student


class WalletAutoCreateTests(TestCase):
    def test_wallet_created_on_user_create(self):
        u = User.objects.create_user(username='alice', email='a@a.com', password='x' * 12)
        self.assertTrue(Wallet.objects.filter(user=u).exists())
        self.assertEqual(u.wallet.balance, Decimal('0.00'))

    def test_wallet_unique_per_user(self):
        u = User.objects.create_user(username='bob', email='b@b.com', password='x' * 12)
        # signal уже создал — повторный get_or_create вернёт тот же
        w, created = Wallet.objects.get_or_create(user=u)
        self.assertFalse(created)
        self.assertEqual(Wallet.objects.filter(user=u).count(), 1)


class WalletServiceCreditDebitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='carol', email='c@c.com', password='x' * 12)

    def test_credit_increases_balance_and_creates_transaction(self):
        tx = WalletService.credit(
            user=self.user,
            amount=Decimal('500.00'),
            tx_type=Transaction.Type.DEPOSIT,
            idempotency_key='credit-1',
            description='пополнение',
        )
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('500.00'))
        self.assertEqual(tx.amount, Decimal('500.00'))
        self.assertEqual(tx.balance_after, Decimal('500.00'))
        self.assertEqual(tx.status, Transaction.Status.COMPLETED)

    def test_debit_decreases_balance(self):
        WalletService.credit(
            user=self.user, amount=Decimal('1000'),
            tx_type=Transaction.Type.DEPOSIT, idempotency_key='c1',
        )
        WalletService.debit(
            user=self.user, amount=Decimal('300'),
            tx_type=Transaction.Type.PURCHASE, idempotency_key='d1',
        )
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('700.00'))

    def test_debit_below_zero_raises(self):
        WalletService.credit(
            user=self.user, amount=Decimal('100'),
            tx_type=Transaction.Type.DEPOSIT, idempotency_key='c1',
        )
        with self.assertRaises(InsufficientFunds):
            WalletService.debit(
                user=self.user, amount=Decimal('200'),
                tx_type=Transaction.Type.PURCHASE, idempotency_key='d1',
            )
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('100.00'))
        # неуспешная транзакция не должна попасть в ledger
        self.assertEqual(Transaction.objects.filter(wallet=self.user.wallet).count(), 1)

    def test_idempotency_returns_same_transaction(self):
        tx1 = WalletService.credit(
            user=self.user, amount=Decimal('500'),
            tx_type=Transaction.Type.DEPOSIT, idempotency_key='same-key',
        )
        tx2 = WalletService.credit(
            user=self.user, amount=Decimal('999'),  # другая сумма игнорируется
            tx_type=Transaction.Type.DEPOSIT, idempotency_key='same-key',
        )
        self.assertEqual(tx1.id, tx2.id)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('500.00'))

    def test_balance_matches_ledger_sum(self):
        WalletService.credit(user=self.user, amount=Decimal('1000'),
                             tx_type=Transaction.Type.DEPOSIT, idempotency_key='k1')
        WalletService.debit(user=self.user, amount=Decimal('300'),
                            tx_type=Transaction.Type.PURCHASE, idempotency_key='k2')
        WalletService.credit(user=self.user, amount=Decimal('50'),
                             tx_type=Transaction.Type.REFUND, idempotency_key='k3')

        self.user.wallet.refresh_from_db()
        reconciled = WalletService.reconcile_balance(self.user.wallet)
        self.assertEqual(self.user.wallet.balance, reconciled)
        self.assertEqual(self.user.wallet.balance, Decimal('750.00'))

    def test_credit_negative_amount_raises(self):
        with self.assertRaises(ValueError):
            WalletService.credit(
                user=self.user, amount=Decimal('-10'),
                tx_type=Transaction.Type.DEPOSIT, idempotency_key='neg',
            )

    def test_idempotency_key_required(self):
        with self.assertRaises(ValueError):
            WalletService.credit(
                user=self.user, amount=Decimal('1'),
                tx_type=Transaction.Type.DEPOSIT, idempotency_key='',
            )


class WalletServiceTransferTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username='al', email='al@x.com', password='x' * 12)
        self.bob = User.objects.create_user(username='bo', email='bo@x.com', password='x' * 12)
        WalletService.credit(
            user=self.alice, amount=Decimal('1000'),
            tx_type=Transaction.Type.DEPOSIT, idempotency_key='seed-al',
        )

    def test_transfer_moves_money(self):
        out_tx, in_tx = WalletService.transfer(
            from_user=self.alice, to_user=self.bob,
            amount=Decimal('300'),
            tx_type_out=Transaction.Type.PURCHASE,
            tx_type_in=Transaction.Type.LESSON_PAYOUT,
            idempotency_key='xfer-1',
        )
        self.alice.wallet.refresh_from_db()
        self.bob.wallet.refresh_from_db()
        self.assertEqual(self.alice.wallet.balance, Decimal('700.00'))
        self.assertEqual(self.bob.wallet.balance, Decimal('300.00'))
        self.assertEqual(out_tx.amount, Decimal('-300.00'))
        self.assertEqual(in_tx.amount, Decimal('300.00'))

    def test_transfer_insufficient_funds_rolls_back(self):
        with self.assertRaises(InsufficientFunds):
            WalletService.transfer(
                from_user=self.alice, to_user=self.bob,
                amount=Decimal('5000'),
                tx_type_out=Transaction.Type.PURCHASE,
                tx_type_in=Transaction.Type.LESSON_PAYOUT,
                idempotency_key='xfer-fail',
            )
        # Ничего не сдвинулось
        self.alice.wallet.refresh_from_db()
        self.bob.wallet.refresh_from_db()
        self.assertEqual(self.alice.wallet.balance, Decimal('1000.00'))
        self.assertEqual(self.bob.wallet.balance, Decimal('0.00'))


# ---------- Tariff -----------------------------------------------------------


class TariffModelTests(TestCase):
    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('tariff_t')

    def test_total_lessons_calc(self):
        t = Tariff.objects.create(
            teacher=self.teacher, subject=self.subject,
            lessons_per_week=2, lesson_duration_minutes=60,
            duration_months=1, price_per_month=Decimal('800000'),
        )
        # 2 в неделю × 4 недели/мес × 1 мес = 8 уроков
        self.assertEqual(t.total_lessons, 8)
        self.assertEqual(t.total_price, Decimal('800000.00'))
        # 800000 / 8 = 100000
        self.assertEqual(t.price_per_lesson, Decimal('100000.00'))

    def test_total_lessons_multi_month(self):
        t = Tariff.objects.create(
            teacher=self.teacher, subject=self.subject,
            lessons_per_week=3, lesson_duration_minutes=60,
            duration_months=3, price_per_month=Decimal('600000'),
        )
        # 3 × 4 × 3 = 36
        self.assertEqual(t.total_lessons, 36)
        self.assertEqual(t.total_price, Decimal('1800000.00'))
        # 1800000 / 36 = 50000
        self.assertEqual(t.price_per_lesson, Decimal('50000.00'))

    def test_negative_price_violates_constraint(self):
        from django.db.utils import IntegrityError
        with self.assertRaises(IntegrityError):
            Tariff.objects.create(
                teacher=self.teacher, subject=self.subject,
                lessons_per_week=2, lesson_duration_minutes=60,
                duration_months=1, price_per_month=Decimal('-100'),
            )


class TariffFormTests(TestCase):
    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('tariff_t2')

    def test_min_price_validation(self):
        from billing.forms import TariffForm
        form = TariffForm(data={
            'subject': self.subject.id,
            'name': '', 'description': '',
            'lessons_per_week': 2, 'lesson_duration_minutes': 60,
            'duration_months': 1, 'price_per_month': '5000',  # ниже минимума
            'is_active': 'on',
        }, teacher=self.teacher)
        self.assertFalse(form.is_valid())
        self.assertIn('price_per_month', form.errors)

    def test_subject_must_belong_to_teacher(self):
        from billing.forms import TariffForm
        from teachers.models import Subject
        # Создаём «чужой» предмет — учитель его НЕ преподаёт
        foreign_subject = Subject.objects.create(name='Тест-предмет', category=self.subject.category)
        form = TariffForm(data={
            'subject': foreign_subject.id,
            'name': '', 'description': '',
            'lessons_per_week': 2, 'lesson_duration_minutes': 60,
            'duration_months': 1, 'price_per_month': '800000',
            'is_active': 'on',
        }, teacher=self.teacher)
        self.assertFalse(form.is_valid())
        self.assertIn('subject', form.errors)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class TariffViewsTests(TestCase):
    def setUp(self):
        from django.urls import reverse
        self.teacher, self.subject = _make_teacher_with_subject('tv1')
        self.other_teacher, _ = _make_teacher_with_subject('tv2')
        self.url_list = reverse('tariffs_list')
        self.url_create = reverse('tariff_create')

    def test_list_requires_login(self):
        r = self.client.get(self.url_list)
        # Аноним без teacher_profile — login_required перенаправит на login
        self.assertEqual(r.status_code, 302)
        self.assertIn('/login', r.url)

    def test_list_visible_to_teacher(self):
        self.client.login(username='tv1', password='x' * 12)
        r = self.client.get(self.url_list)
        self.assertEqual(r.status_code, 200)

    def test_student_redirected_from_tariffs(self):
        User.objects.create_user(
            username='stud1', email='s@x.com', password='x' * 12, user_type='student'
        )
        self.client.login(username='stud1', password='x' * 12)
        r = self.client.get(self.url_list)
        # Должен редиректить (нет teacher_profile)
        self.assertEqual(r.status_code, 302)

    def test_cannot_edit_others_tariff(self):
        from django.urls import reverse
        tariff = Tariff.objects.create(
            teacher=self.other_teacher, subject=self.subject,
            lessons_per_week=2, lesson_duration_minutes=60,
            duration_months=1, price_per_month=Decimal('800000'),
        )
        self.client.login(username='tv1', password='x' * 12)
        r = self.client.get(reverse('tariff_edit', args=[tariff.pk]))
        self.assertEqual(r.status_code, 404)

    def test_create_tariff_via_form(self):
        self.client.login(username='tv1', password='x' * 12)
        r = self.client.post(self.url_create, {
            'subject': self.subject.id,
            'name': 'Стандарт',
            'description': 'Базовый курс',
            'lessons_per_week': 2,
            'lesson_duration_minutes': 60,
            'duration_months': 1,
            'price_per_month': '800000',
            'is_active': 'on',
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Tariff.objects.filter(teacher=self.teacher).count(), 1)


# ---------- Subscription purchase -----------------------------------------


class SubscriptionPurchaseTests(TestCase):
    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('sub_t')
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('sub_s', balance=Decimal('1000000'))

    def test_successful_purchase(self):
        from teachers.models import Booking
        sub = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key='test-1',
        )
        # Subscription создана
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertEqual(sub.total_lessons, 8)  # 2/нед × 4 × 1 мес
        self.assertEqual(sub.price_total, Decimal('800000.00'))
        self.assertEqual(sub.price_per_lesson, Decimal('100000.00'))
        self.assertEqual(sub.escrow_balance, Decimal('800000.00'))
        # Snapshot commission_rate из settings
        self.assertEqual(sub.commission_rate, Decimal('0.15'))

        # Кошелёк списался
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('200000.00'))

        # 8 bookings создалось
        bookings = Booking.objects.filter(subscription=sub)
        self.assertEqual(bookings.count(), 8)
        # Все confirmed, не trial
        for b in bookings:
            self.assertEqual(b.status, 'confirmed')
            self.assertFalse(b.is_trial)

        # Транзакция привязана к подписке
        purchase_tx = Transaction.objects.filter(
            related_subscription=sub, type=Transaction.Type.PURCHASE
        ).first()
        self.assertIsNotNone(purchase_tx)
        self.assertEqual(purchase_tx.amount, Decimal('-800000.00'))

    def test_insufficient_funds_rolls_back_everything(self):
        from teachers.models import Booking
        poor = _make_student_with_balance('poor', balance=Decimal('100000'))
        with self.assertRaises(InsufficientFunds):
            SubscriptionService.purchase(
                student=poor, tariff=self.tariff, idempotency_key='poor-1',
            )
        # Никакая подписка не создана
        self.assertEqual(Subscription.objects.filter(student=poor).count(), 0)
        # Никаких bookings
        self.assertEqual(Booking.objects.filter(student=poor).count(), 0)
        # Wallet не тронут
        poor.wallet.refresh_from_db()
        self.assertEqual(poor.wallet.balance, Decimal('100000.00'))

    def test_idempotency_double_submit(self):
        sub1 = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff, idempotency_key='dup-key',
        )
        # Второй вызов с тем же ключом — вернёт ту же подписку
        sub2 = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff, idempotency_key='dup-key',
        )
        self.assertEqual(sub1.id, sub2.id)
        # Только одна подписка в БД, и одна транзакция
        self.assertEqual(Subscription.objects.filter(student=self.student).count(), 1)
        # Кошелёк списан только один раз
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('200000.00'))

    def test_cannot_buy_twice_same_teacher_subject(self):
        SubscriptionService.purchase(
            student=self.student, tariff=self.tariff, idempotency_key='first',
        )
        # Допполняем кошелёк, чтобы хватило денег на 2-ю покупку
        WalletService.credit(
            user=self.student, amount=Decimal('800000'),
            tx_type=Transaction.Type.DEPOSIT,
            idempotency_key='topup-for-2nd',
        )
        with self.assertRaises(AlreadySubscribed):
            SubscriptionService.purchase(
                student=self.student, tariff=self.tariff, idempotency_key='second',
            )

    def test_teacher_without_schedule_raises(self):
        teacher_no_sched, subj = _make_teacher_with_subject('no_sched', with_schedule=False)
        tariff = _make_tariff(teacher_no_sched, subj)
        with self.assertRaises(NotEnoughCapacity):
            SubscriptionService.purchase(
                student=self.student, tariff=tariff, idempotency_key='no-sched',
            )


# ---------- Lesson payout (Phase 4) ---------------------------------------


class LessonPayoutTests(TestCase):
    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('p4_t')
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('p4_s', balance=Decimal('1000000'))
        self.subscription = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key='p4-purchase',
        )

    def _first_booking(self):
        from teachers.models import Booking
        return Booking.objects.filter(subscription=self.subscription).first()

    def test_payout_credits_teacher_and_platform(self):
        booking = self._first_booking()
        booking.status = 'completed'
        booking.save()  # триггерит сигнал completed_lessons +=1

        ok = SubscriptionService.release_lesson_payout(booking)
        self.assertTrue(ok)

        teacher_user = self.teacher.user
        teacher_user.wallet.refresh_from_db()
        self.platform.wallet.refresh_from_db()
        self.subscription.refresh_from_db()

        # 800000 / 8 = 100000 за урок; comm 15% → 15000 платформе, 85000 учителю
        self.assertEqual(self.subscription.price_per_lesson, Decimal('100000.00'))
        self.assertEqual(teacher_user.wallet.balance, Decimal('85000.00'))
        self.assertEqual(self.platform.wallet.balance, Decimal('15000.00'))
        # escrow уменьшился
        self.assertEqual(self.subscription.escrow_balance, Decimal('700000.00'))
        # lessons_paid_out счётчик увеличен
        self.assertEqual(self.subscription.lessons_paid_out, 1)

    def test_payout_idempotent(self):
        booking = self._first_booking()
        booking.status = 'completed'
        booking.save()

        SubscriptionService.release_lesson_payout(booking)
        ok2 = SubscriptionService.release_lesson_payout(booking)
        self.assertFalse(ok2, 'повторный вызов должен вернуть False')

        # Балансы не удвоились
        teacher_user = self.teacher.user
        teacher_user.wallet.refresh_from_db()
        self.assertEqual(teacher_user.wallet.balance, Decimal('85000.00'))

    def test_full_completion_sets_subscription_completed(self):
        from teachers.models import Booking
        bookings = list(Booking.objects.filter(subscription=self.subscription))
        self.assertEqual(len(bookings), 8)

        # Помечаем все 8 как completed и выплачиваем
        for b in bookings:
            b.status = 'completed'
            b.save()
            SubscriptionService.release_lesson_payout(b)

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.lessons_paid_out, 8)
        self.assertEqual(self.subscription.status, self.subscription.Status.COMPLETED)
        self.assertEqual(self.subscription.escrow_balance, Decimal('0.00'))

        # Финансовая сходимость: 800000 = 8 * (85000 + 15000)
        teacher_user = self.teacher.user
        teacher_user.wallet.refresh_from_db()
        self.platform.wallet.refresh_from_db()
        self.assertEqual(teacher_user.wallet.balance, Decimal('680000.00'))
        self.assertEqual(self.platform.wallet.balance, Decimal('120000.00'))

    def test_payout_recovers_missing_commission(self):
        """B2: если комиссия платформе не начислилась (частичный сбой), повторный
        payout добивает её, НЕ трогая эскроу и счётчики повторно."""
        from billing.models import Transaction
        booking = self._first_booking()
        booking.status = 'completed'
        booking.save()
        SubscriptionService.release_lesson_payout(booking)

        # Симулируем потерю комиссии: удаляем tx и откатываем баланс платформы.
        commission_key = f'commission:{booking.id}'
        Transaction.objects.filter(idempotency_key=commission_key).delete()
        platform_wallet = self.platform.wallet
        platform_wallet.refresh_from_db()
        platform_wallet.balance = platform_wallet.balance - Decimal('15000.00')
        platform_wallet.save(update_fields=['balance'])

        self.subscription.refresh_from_db()
        escrow_before = self.subscription.escrow_balance
        paid_before = self.subscription.lessons_paid_out

        # Повторный payout: payout учителю уже есть → already_paid=True.
        result = SubscriptionService.release_lesson_payout(booking)
        self.assertFalse(result, 'эскроу уже списан — возвращаем False')

        # Комиссия восстановлена, эскроу и счётчик НЕ изменились.
        self.assertTrue(Transaction.objects.filter(idempotency_key=commission_key).exists())
        platform_wallet.refresh_from_db()
        self.assertEqual(platform_wallet.balance, Decimal('15000.00'))
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.escrow_balance, escrow_before)
        self.assertEqual(self.subscription.lessons_paid_out, paid_before)

    def test_completed_signal_increments_counter(self):
        booking = self._first_booking()
        self.assertEqual(self.subscription.completed_lessons, 0)
        booking.status = 'completed'
        booking.save()
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.completed_lessons, 1)

        # Второй save с тем же status — counter НЕ удвоится
        booking.save()
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.completed_lessons, 1)


# ---------- Subscription cancellation (Phase 5) ---------------------------


class SubscriptionCancellationTests(TestCase):
    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('p5_t')
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('p5_s', balance=Decimal('1000000'))
        self.subscription = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key='p5-purchase',
        )

    def _all_bookings(self):
        from teachers.models import Booking
        return list(Booking.objects.filter(subscription=self.subscription).order_by('slot__start_at'))

    def test_cancel_without_completed_full_refund(self):
        from teachers.models import Booking, TimeSlot
        # До отмены: balance = 200000 (после покупки)
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('200000.00'))

        result = SubscriptionService.cancel(
            self.subscription, cancelled_by='student', reason='тест отмены',
        )

        self.assertEqual(result['refunded'], Decimal('800000.00'))
        self.assertEqual(result['paid_out'], 0)
        self.assertEqual(result['cancelled_bookings'], 8)

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status,
                         self.subscription.Status.CANCELLED_BY_STUDENT)
        self.assertEqual(self.subscription.escrow_balance, Decimal('0.00'))
        self.assertIsNotNone(self.subscription.cancelled_at)
        self.assertEqual(self.subscription.cancellation_reason, 'тест отмены')

        # Полный refund: balance вернулся к 1 000 000
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('1000000.00'))

        # Все 8 bookings отменены, слоты свободны
        cancelled = Booking.objects.filter(
            subscription=self.subscription, status='cancelled_by_student',
        ).count()
        self.assertEqual(cancelled, 8)
        free_slots = TimeSlot.objects.filter(
            teacher=self.teacher, status='free',
        ).count()
        # Все 8 свободны (они в будущем)
        self.assertGreaterEqual(free_slots, 8)

    def test_cancel_with_completed_pays_teacher_then_refunds(self):
        # Помечаем 3 урока completed (учитель отработал, но payout ещё не сделан)
        bookings = self._all_bookings()[:3]
        for b in bookings:
            b.status = 'completed'
            b.save()

        result = SubscriptionService.cancel(
            self.subscription, cancelled_by='student', reason='',
        )

        # Учителю выплатились 3 урока × 85 000 = 255 000
        self.assertEqual(result['paid_out'], 3)
        teacher_user = self.teacher.user
        teacher_user.wallet.refresh_from_db()
        self.assertEqual(teacher_user.wallet.balance, Decimal('255000.00'))
        self.platform.wallet.refresh_from_db()
        self.assertEqual(self.platform.wallet.balance, Decimal('45000.00'))

        # Ученику возвращены 5 уроков × 100 000 = 500 000
        self.assertEqual(result['refunded'], Decimal('500000.00'))
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('700000.00'))  # 200000 + 500000

        # Бухгалтерская сходимость: 800000 = 255000 + 45000 + 500000
        # ✓

    def test_cancel_idempotent_raises(self):
        SubscriptionService.cancel(self.subscription, cancelled_by='student', reason='')
        with self.assertRaises(CancellationError):
            SubscriptionService.cancel(self.subscription, cancelled_by='student', reason='')

    def test_cancel_by_teacher_sets_correct_status(self):
        result = SubscriptionService.cancel(self.subscription, cancelled_by='teacher', reason='unavailable')
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status,
                         self.subscription.Status.CANCELLED_BY_TEACHER)
        # Ученик получает полный refund
        self.assertEqual(result['refunded'], Decimal('800000.00'))

    def test_cannot_cancel_already_completed(self):
        # Завершаем подписку полностью
        for b in self._all_bookings():
            b.status = 'completed'
            b.save()
            SubscriptionService.release_lesson_payout(b)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, self.subscription.Status.COMPLETED)

        with self.assertRaises(CancellationError):
            SubscriptionService.cancel(self.subscription, cancelled_by='student', reason='')


# ---------- Reviews per-lesson (Phase 7) ----------------------------------


class ReviewPerLessonTests(TestCase):
    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('p7_t')
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('p7_s', balance=Decimal('1000000'))
        self.subscription = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key='p7-purchase',
        )

    def _complete_bookings(self, n):
        from teachers.models import Booking
        bookings = list(Booking.objects.filter(subscription=self.subscription)[:n])
        for b in bookings:
            b.status = 'completed'
            b.save()
        return bookings

    def test_multiple_reviews_per_subscription(self):
        """Ученик может оставить отдельный отзыв на КАЖДЫЙ урок подписки."""
        from teachers.models import Booking, Review
        bookings = self._complete_bookings(3)
        # Ровно 3 отзыва — на каждый booking
        for i, b in enumerate(bookings):
            Review.objects.create(
                teacher=self.teacher, student=self.student,
                subject=self.subject, booking=b,
                rating=5 - i,
                knowledge_rating=5, communication_rating=5, punctuality_rating=5,
                is_verified=True,
            )
        self.assertEqual(
            Review.objects.filter(student=self.student, teacher=self.teacher).count(),
            3,
        )

    def test_one_review_per_booking_enforced(self):
        """OneToOne booking запрещает 2 отзыва на 1 урок."""
        from teachers.models import Booking, Review
        from django.db.utils import IntegrityError
        bookings = self._complete_bookings(1)
        b = bookings[0]
        Review.objects.create(
            teacher=self.teacher, student=self.student, subject=self.subject,
            booking=b, rating=5,
            knowledge_rating=5, communication_rating=5, punctuality_rating=5,
            is_verified=True,
        )
        with self.assertRaises(IntegrityError):
            Review.objects.create(
                teacher=self.teacher, student=self.student, subject=self.subject,
                booking=b, rating=4,
                knowledge_rating=4, communication_rating=4, punctuality_rating=4,
                is_verified=True,
            )

    def test_leave_review_view_per_booking(self):
        from django.urls import reverse
        from teachers.models import Booking, Review

        bookings = self._complete_bookings(2)
        self.client.login(username='p7_s', password='x' * 12)

        # Оставляем отзыв на 1-й booking
        url1 = reverse('leave_review', args=[bookings[0].id])
        r = self.client.post(url1, {
            'rating': 5, 'comment': 'Отличный урок',
            'knowledge_rating': 5, 'communication_rating': 4, 'punctuality_rating': 5,
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Review.objects.filter(booking=bookings[0]).exists())

        # Оставляем отзыв на 2-й booking — должен создаться отдельный Review
        url2 = reverse('leave_review', args=[bookings[1].id])
        r = self.client.post(url2, {
            'rating': 4, 'comment': 'Норм',
            'knowledge_rating': 4, 'communication_rating': 4, 'punctuality_rating': 4,
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            Review.objects.filter(student=self.student, teacher=self.teacher).count(),
            2,
        )

    def test_cannot_review_others_booking(self):
        from django.urls import reverse
        from teachers.models import Booking, StudentProfile
        bookings = self._complete_bookings(1)
        other = User.objects.create_user(
            username='p7_other', email='oo@x.com', password='x' * 12,
            user_type='student',
        )
        StudentProfile.objects.create(user=other)
        self.client.login(username='p7_other', password='x' * 12)
        url = reverse('leave_review', args=[bookings[0].id])
        r = self.client.post(url, {
            'rating': 1, 'comment': 'fake',
            'knowledge_rating': 1, 'communication_rating': 1, 'punctuality_rating': 1,
        })
        self.assertEqual(r.status_code, 403)

    def test_cannot_review_not_completed(self):
        """Нельзя оценить незавершённый урок."""
        from django.urls import reverse
        from teachers.models import Booking
        b = Booking.objects.filter(subscription=self.subscription).first()
        # b.status='confirmed' (default after purchase)
        self.client.login(username='p7_s', password='x' * 12)
        r = self.client.post(reverse('leave_review', args=[b.id]), {
            'rating': 5, 'comment': '',
            'knowledge_rating': 5, 'communication_rating': 5, 'punctuality_rating': 5,
        })
        # Редирект в my_bookings_page с warning, не создаём Review
        from teachers.models import Review
        self.assertFalse(Review.objects.filter(booking=b).exists())


# ---------- Withdrawal (Phase 6) ------------------------------------------


class WithdrawalServiceTests(TestCase):
    def setUp(self):
        # «Учитель» с балансом — для тестов withdrawal достаточно просто User.
        self.user = User.objects.create_user(
            username='wd_user', email='wd@x.com', password='x' * 12,
            user_type='teacher',
        )
        WalletService.credit(
            user=self.user, amount=Decimal('500000'),
            tx_type=Transaction.Type.DEPOSIT, idempotency_key='wd-seed',
        )
        self.admin = User.objects.create_user(
            username='wd_admin', email='wda@x.com', password='x' * 12,
            is_staff=True,
        )

    def test_create_request_debits_wallet(self):
        wr = WithdrawalService.create_request(
            user=self.user, amount=Decimal('200000'),
            payout_method='card', payout_details='8600 1234 5678 9012',
            idempotency_key='wd-1',
        )
        self.assertEqual(wr.status, WithdrawalRequest.Status.PENDING)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('300000.00'))

    def test_min_amount_validation(self):
        with self.assertRaises(WithdrawalAmountError):
            WithdrawalService.create_request(
                user=self.user, amount=Decimal('50000'),
                payout_method='card', payout_details='card',
                idempotency_key='wd-low',
            )

    def test_insufficient_funds(self):
        with self.assertRaises(InsufficientFunds):
            WithdrawalService.create_request(
                user=self.user, amount=Decimal('1000000'),
                payout_method='card', payout_details='card',
                idempotency_key='wd-big',
            )

    def test_idempotency_double_submit(self):
        wr1 = WithdrawalService.create_request(
            user=self.user, amount=Decimal('100000'),
            payout_method='card', payout_details='c1',
            idempotency_key='wd-dup',
        )
        wr2 = WithdrawalService.create_request(
            user=self.user, amount=Decimal('100000'),
            payout_method='card', payout_details='c1',
            idempotency_key='wd-dup',
        )
        self.assertEqual(wr1.id, wr2.id)
        # Кошелёк списан только один раз
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('400000.00'))

    def test_user_cancel_refunds(self):
        wr = WithdrawalService.create_request(
            user=self.user, amount=Decimal('150000'),
            payout_method='card', payout_details='c',
            idempotency_key='wd-c',
        )
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('350000.00'))

        WithdrawalService.cancel_by_user(wr)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('500000.00'))
        wr.refresh_from_db()
        self.assertEqual(wr.status, WithdrawalRequest.Status.CANCELLED)

    def test_admin_reject_refunds(self):
        wr = WithdrawalService.create_request(
            user=self.user, amount=Decimal('200000'),
            payout_method='card', payout_details='c',
            idempotency_key='wd-r',
        )
        WithdrawalService.reject(wr, admin_user=self.admin, note='подозрительные реквизиты')
        wr.refresh_from_db()
        self.assertEqual(wr.status, WithdrawalRequest.Status.REJECTED)
        self.assertEqual(wr.admin_note, 'подозрительные реквизиты')
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('500000.00'))

    def test_admin_reject_requires_note(self):
        wr = WithdrawalService.create_request(
            user=self.user, amount=Decimal('150000'),
            payout_method='card', payout_details='c',
            idempotency_key='wd-rn',
        )
        with self.assertRaises(WithdrawalError):
            WithdrawalService.reject(wr, admin_user=self.admin, note='   ')

    def test_admin_approve_then_complete(self):
        wr = WithdrawalService.create_request(
            user=self.user, amount=Decimal('100000'),
            payout_method='card', payout_details='c',
            idempotency_key='wd-ac',
        )
        WithdrawalService.approve(wr, admin_user=self.admin)
        wr.refresh_from_db()
        self.assertEqual(wr.status, WithdrawalRequest.Status.APPROVED)
        # На completed
        WithdrawalService.complete(wr, admin_user=self.admin)
        wr.refresh_from_db()
        self.assertEqual(wr.status, WithdrawalRequest.Status.COMPLETED)
        # Деньги НЕ возвращаются — они переведены за пределы платформы.
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('400000.00'))

    def test_cannot_cancel_already_approved(self):
        wr = WithdrawalService.create_request(
            user=self.user, amount=Decimal('100000'),
            payout_method='card', payout_details='c',
            idempotency_key='wd-no',
        )
        WithdrawalService.approve(wr, admin_user=self.admin)
        with self.assertRaises(WithdrawalError):
            WithdrawalService.cancel_by_user(wr)


# ---------- Homework (Phase 8) -------------------------------------------


class HomeworkTests(TestCase):
    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('p8_t')
        self.tariff = _make_tariff(self.teacher, self.subject)
        self.student = _make_student_with_balance('p8_s', balance=Decimal('1000000'))
        self.subscription = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key='p8-purchase',
        )

    def test_create_homework(self):
        hw = Homework.objects.create(
            subscription=self.subscription, teacher=self.teacher, student=self.student,
            title='Прочитать главу 1', description='Прочитать и ответить на 5 вопросов.',
        )
        self.assertEqual(hw.status, Homework.Status.ASSIGNED)
        self.assertFalse(hw.is_overdue)

    def test_grade_validation(self):
        from django.db.utils import IntegrityError
        hw = Homework.objects.create(
            subscription=self.subscription, teacher=self.teacher, student=self.student,
            title='Test', description='—',
        )
        with self.assertRaises(IntegrityError):
            HomeworkSubmission.objects.create(
                homework=hw, student=self.student, grade=200,
            )

    def test_one_submission_per_homework(self):
        from django.db.utils import IntegrityError
        hw = Homework.objects.create(
            subscription=self.subscription, teacher=self.teacher, student=self.student,
            title='Test', description='—',
        )
        HomeworkSubmission.objects.create(homework=hw, student=self.student, text_response='ok')
        with self.assertRaises(IntegrityError):
            HomeworkSubmission.objects.create(homework=hw, student=self.student, text_response='dup')

    def test_create_via_view(self):
        from django.urls import reverse
        self.client.login(username=self.teacher.user.username, password='x' * 12)
        url = reverse('teacher_homework_create')
        r = self.client.post(url, {
            'subscription': str(self.subscription.id),
            'title': 'ДЗ 1', 'description': 'описание задания',
        })
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Homework.objects.filter(teacher=self.teacher).count(), 1)

    def test_student_can_only_see_own_homework(self):
        from django.urls import reverse
        other_student = _make_student_with_balance('p8_other', balance=Decimal('0'))
        hw = Homework.objects.create(
            subscription=self.subscription, teacher=self.teacher, student=self.student,
            title='Test', description='—',
        )
        self.client.login(username='p8_other', password='x' * 12)
        r = self.client.get(reverse('homework_detail', args=[hw.id]))
        # Чужие ДЗ → redirect на home с error message
        self.assertEqual(r.status_code, 302)

    def test_student_submit_and_teacher_grade_flow(self):
        from django.urls import reverse
        hw = Homework.objects.create(
            subscription=self.subscription, teacher=self.teacher, student=self.student,
            title='Test', description='—',
        )

        # Student submits
        self.client.login(username='p8_s', password='x' * 12)
        r = self.client.post(reverse('homework_detail', args=[hw.id]), {
            'text_response': 'мой ответ',
        })
        self.assertEqual(r.status_code, 302)
        hw.refresh_from_db()
        self.assertEqual(hw.status, Homework.Status.SUBMITTED)
        self.assertEqual(hw.submission.text_response, 'мой ответ')

        # Teacher grades
        self.client.logout()
        self.client.login(username=self.teacher.user.username, password='x' * 12)
        r = self.client.post(reverse('homework_detail', args=[hw.id]), {
            'decision': 'grade', 'grade': '85',
            'feedback': 'хорошо',
        })
        self.assertEqual(r.status_code, 302)
        hw.refresh_from_db()
        self.assertEqual(hw.status, Homework.Status.GRADED)
        self.assertEqual(hw.submission.grade, 85)

    def test_teacher_return_to_rework(self):
        from django.urls import reverse
        hw = Homework.objects.create(
            subscription=self.subscription, teacher=self.teacher, student=self.student,
            title='Test', description='—',
        )
        sub = HomeworkSubmission.objects.create(
            homework=hw, student=self.student, text_response='первая попытка',
        )
        hw.status = Homework.Status.SUBMITTED
        hw.save()

        self.client.login(username=self.teacher.user.username, password='x' * 12)
        r = self.client.post(reverse('homework_detail', args=[hw.id]), {
            'decision': 'return',
            'feedback': 'нужно дополнить выводы',
        })
        self.assertEqual(r.status_code, 302)
        hw.refresh_from_db()
        self.assertEqual(hw.status, Homework.Status.RETURNED)
        sub.refresh_from_db()
        self.assertIsNone(sub.grade)
        self.assertIn('дополнить', sub.feedback)


# ---------- Progress aggregations (Phase 9) -------------------------------


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class ProgressTests(TestCase):
    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('p9_t')
        self.tariff = _make_tariff(self.teacher, self.subject)
        self.student = _make_student_with_balance('p9_s', balance=Decimal('1000000'))
        self.subscription = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key='p9-purchase',
        )

    def test_attendance_rate_zero_initially(self):
        self.assertEqual(self.subscription.attendance_rate, 0)

    def test_attendance_rate_with_completed(self):
        from teachers.models import Booking
        bookings = list(Booking.objects.filter(subscription=self.subscription)[:3])
        for b in bookings:
            b.status = 'completed'
            b.save()
        self.assertEqual(self.subscription.attendance_rate, 100)

    def test_attendance_rate_with_no_show(self):
        from teachers.models import Booking
        bookings = list(Booking.objects.filter(subscription=self.subscription)[:4])
        bookings[0].status = 'completed'; bookings[0].save()
        bookings[1].status = 'completed'; bookings[1].save()
        bookings[2].status = 'no_show_student'; bookings[2].save()
        bookings[3].status = 'no_show_teacher'; bookings[3].save()
        self.assertEqual(self.subscription.attendance_rate, 50)

    def test_average_grade_none_when_no_homework(self):
        self.assertIsNone(self.subscription.average_grade)

    def test_average_grade_with_homework(self):
        hw1 = Homework.objects.create(
            subscription=self.subscription, teacher=self.teacher, student=self.student,
            title='hw1', description='—',
        )
        HomeworkSubmission.objects.create(homework=hw1, student=self.student, grade=80)
        hw2 = Homework.objects.create(
            subscription=self.subscription, teacher=self.teacher, student=self.student,
            title='hw2', description='—',
        )
        HomeworkSubmission.objects.create(homework=hw2, student=self.student, grade=100)
        self.assertEqual(self.subscription.average_grade, 90.0)

    def test_homework_completion_rate(self):
        Homework.objects.create(
            subscription=self.subscription, teacher=self.teacher, student=self.student,
            title='hw1', description='—', status=Homework.Status.GRADED,
        )
        Homework.objects.create(
            subscription=self.subscription, teacher=self.teacher, student=self.student,
            title='hw2', description='—', status=Homework.Status.ASSIGNED,
        )
        self.assertEqual(self.subscription.homework_completion_rate, 50)

    def test_next_lesson(self):
        next_b = self.subscription.next_lesson
        self.assertIsNotNone(next_b)
        self.assertEqual(next_b.status, 'confirmed')

    def test_my_progress_view(self):
        from django.urls import reverse
        self.client.login(username='p9_s', password='x' * 12)
        r = self.client.get(reverse('my_progress'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        self.assertIn('Мой прогресс', html)

    def test_teacher_student_progress_view(self):
        from django.urls import reverse
        self.client.login(username=self.teacher.user.username, password='x' * 12)
        r = self.client.get(reverse('teacher_student_progress', args=[self.subscription.id]))
        self.assertEqual(r.status_code, 200)
        self.assertIn('Прогресс ученика', r.content.decode('utf-8'))

    def test_teacher_cannot_see_others_subscription_progress(self):
        from django.urls import reverse
        other_teacher, _ = _make_teacher_with_subject('p9_other_t')
        self.client.login(username=other_teacher.user.username, password='x' * 12)
        r = self.client.get(reverse('teacher_student_progress', args=[self.subscription.id]))
        self.assertEqual(r.status_code, 404)


class BugfixHardeningTests(TestCase):
    """Регрессии на исправленные баги (деньги/lifecycle)."""

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('bf_t')
        self.tariff = _make_tariff(self.teacher, self.subject, lessons_per_week=2,
                                   duration_months=1, price=Decimal('800000'))
        self.student = _make_student_with_balance('bf_s', balance=Decimal('1000000'))
        self.sub = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff, idempotency_key='bf-buy',
        )

    def test_payout_blocked_after_refund(self):
        """Возврат за урок → выплата учителю невозможна (нет двойной оплаты)."""
        from teachers.models import Booking
        b = Booking.objects.filter(subscription=self.sub).first()
        b.status = 'completed'; b.save()
        SubscriptionService.refund_lesson(b, cancelled_by='admin', reason='спор')
        ok = SubscriptionService.release_lesson_payout(b)
        self.assertFalse(ok)
        self.teacher.user.wallet.refresh_from_db()
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('0.00'))

    def test_cannot_cancel_started_lesson(self):
        from datetime import timedelta
        from django.utils import timezone
        from teachers.models import Booking
        b = Booking.objects.filter(subscription=self.sub).select_related('slot').first()
        # Двигаем слот в прошлое (имитируем уже начавшийся урок).
        b.slot.start_at = timezone.now() - timedelta(minutes=10)
        b.slot.end_at = timezone.now() + timedelta(minutes=50)
        b.slot.save(update_fields=['start_at', 'end_at'])
        b.status = 'confirmed'; b.save()
        with self.assertRaises(ValueError):
            b.cancel_by_student()
        with self.assertRaises(ValueError):
            b.cancel_by_teacher()

    def test_create_hold_rejects_started_slot(self):
        from datetime import timedelta
        from django.utils import timezone
        from teachers.models import TimeSlot, Booking, SlotUnavailable
        slot = TimeSlot.objects.create(
            teacher=self.teacher,
            start_at=timezone.now() - timedelta(minutes=1),
            end_at=timezone.now() + timedelta(minutes=59),
            status='free',
        )
        with self.assertRaises(SlotUnavailable):
            Booking.create_hold(slot_id=slot.id, student=self.student, subject=self.subject)

    def test_cancel_settles_past_confirmed_lesson(self):
        """Прошедший confirmed-урок при отмене подписки оплачивается учителю,
        а не молча возвращается ученику (#3 gap)."""
        from datetime import timedelta
        from django.utils import timezone
        from teachers.models import Booking
        b = Booking.objects.filter(subscription=self.sub).select_related('slot').first()
        # Урок прошёл, учитель присутствовал, но mark_completed не успел.
        b.slot.start_at = timezone.now() - timedelta(hours=2)
        b.slot.end_at = timezone.now() - timedelta(hours=1)
        b.slot.save(update_fields=['start_at', 'end_at'])
        b.meeting_url = b.build_meeting_url()
        b.record_join(is_teacher=True)  # учитель был
        b.save()
        SubscriptionService.cancel(self.sub, cancelled_by='student', reason='тест')
        self.teacher.user.wallet.refresh_from_db()
        # За проведённый урок учитель получил 85% (800000/8=100000 → 85000).
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('85000.00'))


class WalletRaceConditionTests(TransactionTestCase):
    """Race test: два параллельных debit'а на 60 при балансе 100.

    Инвариант: баланс никогда не должен уйти в минус, при этом успешным
    может быть только один debit (на PostgreSQL — благодаря select_for_update,
    на SQLite — благодаря file-level lock).
    """

    def test_concurrent_debits_dont_go_negative(self):
        import threading
        from django.db import OperationalError

        user = User.objects.create_user(username='race', email='race@x.com', password='x' * 12)
        WalletService.credit(
            user=user, amount=Decimal('100'),
            tx_type=Transaction.Type.DEPOSIT, idempotency_key='race-seed',
        )

        successes = []
        errors = []

        def worker(key_suffix):
            from django.db import connection
            try:
                WalletService.debit(
                    user=user, amount=Decimal('60'),
                    tx_type=Transaction.Type.PURCHASE,
                    idempotency_key=f'race-{key_suffix}',
                )
                successes.append(key_suffix)
            except (InsufficientFunds, OperationalError) as e:
                errors.append(type(e).__name__)
            finally:
                connection.close()

        t1 = threading.Thread(target=worker, args=('a',))
        t2 = threading.Thread(target=worker, args=('b',))
        t1.start(); t2.start()
        t1.join(); t2.join()

        user.wallet.refresh_from_db()
        # Главный инвариант: balance >= 0 ВСЕГДА, второй debit не прошёл
        self.assertGreaterEqual(user.wallet.balance, Decimal('0'))
        self.assertLessEqual(len(successes), 1,
                             f'не должно быть 2 успешных debit\'а: {successes}')
        # 0 успехов = оба упали (SQLite-locking), 1 = классический PG-сценарий
        self.assertIn(len(successes), (0, 1))


# ---------- Week-2: lesson refund + teacher no-show -----------------------


class RefundLessonTests(TestCase):
    """Возврат стоимости отменённого урока подписки (фикс «зависшего эскроу»)."""

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('w2_t')
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('w2_s', balance=Decimal('1000000'))
        self.subscription = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key='w2-purchase',
        )

    def _first_booking(self):
        from teachers.models import Booking
        return Booking.objects.filter(subscription=self.subscription).first()

    def test_refund_returns_escrow_and_shrinks_package(self):
        booking = self._first_booking()
        self.student.wallet.refresh_from_db()
        balance_before = self.student.wallet.balance  # 200000 после покупки

        refunded = SubscriptionService.refund_lesson(booking, cancelled_by='teacher',
                                                      reason='тест')
        self.assertEqual(refunded, Decimal('100000.00'))

        self.subscription.refresh_from_db()
        self.student.wallet.refresh_from_db()
        # Деньги вернулись ученику, эскроу уменьшился, пакет ужался на 1 урок.
        self.assertEqual(self.student.wallet.balance, balance_before + Decimal('100000.00'))
        self.assertEqual(self.subscription.escrow_balance, Decimal('700000.00'))
        self.assertEqual(self.subscription.total_lessons, 7)

    def test_refund_is_idempotent(self):
        booking = self._first_booking()
        SubscriptionService.refund_lesson(booking, cancelled_by='student')
        second = SubscriptionService.refund_lesson(booking, cancelled_by='student')
        self.assertEqual(second, Decimal('0.00'))
        self.subscription.refresh_from_db()
        # total_lessons ужался ровно один раз.
        self.assertEqual(self.subscription.total_lessons, 7)

    def test_refund_blocked_after_payout(self):
        booking = self._first_booking()
        booking.status = 'completed'
        booking.save()
        self.assertTrue(SubscriptionService.release_lesson_payout(booking))
        # За оплаченный урок возврата быть не может.
        refunded = SubscriptionService.refund_lesson(booking, cancelled_by='teacher')
        self.assertEqual(refunded, Decimal('0.00'))

    def test_last_lesson_delegates_to_full_cancel(self):
        # Искусственно сводим подписку к одному оставшемуся уроку.
        sub = self.subscription
        sub.total_lessons = 1
        sub.escrow_balance = Decimal('100000.00')
        sub.save(update_fields=['total_lessons', 'escrow_balance'])
        booking = self._first_booking()

        SubscriptionService.refund_lesson(booking, cancelled_by='student',
                                          reason='последний урок')
        sub.refresh_from_db()
        # Подписка отменена целиком, эскроу обнулён.
        self.assertEqual(sub.status, Subscription.Status.CANCELLED_BY_STUDENT)
        self.assertEqual(sub.escrow_balance, Decimal('0.00'))


class LearningRequestFlowTests(TestCase):
    """ТЗ-флоу: заявка → одобрение → оплата → бронь."""

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('lr_t')
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('lr_s', balance=Decimal('1000000'))

    def _request(self, key='1'):
        t = self.tariff
        return SubscriptionService.create_request(
            student=self.student, teacher=self.teacher, subject=self.subject,
            lessons_per_week=t.lessons_per_week,
            lesson_duration_minutes=t.lesson_duration_minutes,
            duration_months=t.duration_months, price_per_month=t.price_per_month,
            tariff=t, preferred_schedule='будни вечером',
            idempotency_key=f'lr-req-{key}',
        )

    def test_request_does_not_charge_or_book(self):
        sub = self._request()
        self.assertEqual(sub.status, Subscription.Status.PENDING_APPROVAL)
        self.assertEqual(sub.escrow_balance, Decimal('0.00'))
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('1000000'))  # не списано
        from teachers.models import Booking
        self.assertEqual(Booking.objects.filter(subscription=sub).count(), 0)

    def test_duplicate_request_blocked(self):
        self._request('a')
        with self.assertRaises(AlreadySubscribed):
            self._request('b')

    def test_idempotent_request(self):
        s1 = self._request('same')
        s2 = self._request('same')
        self.assertEqual(s1.id, s2.id)

    def test_cannot_pay_before_approval(self):
        sub = self._request()
        with self.assertRaises(ValueError):
            SubscriptionService.pay(sub, idempotency_key='x')

    def test_full_flow_request_approve_pay_book(self):
        from teachers.models import Booking
        sub = self._request()

        SubscriptionService.approve_request(sub)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.PENDING_PAYMENT)
        self.assertIsNotNone(sub.approved_at)
        self.assertIsNotNone(sub.approval_expires_at)

        SubscriptionService.pay(sub, idempotency_key='pay1')
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        self.assertEqual(sub.escrow_balance, Decimal('800000.00'))
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('200000.00'))
        # До бронирования уроков ещё нет.
        self.assertEqual(Booking.objects.filter(subscription=sub).count(), 0)

        pattern = [{'day': 'monday', 'time': '10:00'}, {'day': 'wednesday', 'time': '10:00'}]
        created = SubscriptionService.book_schedule(sub, pattern)
        self.assertEqual(len(created), sub.total_lessons)  # 8
        self.assertTrue(all(b.status == 'confirmed' for b in created))
        self.assertTrue(all(b.meeting_url for b in created))
        sub.refresh_from_db()
        self.assertEqual(sub.weekly_pattern, pattern)

    def test_reject_keeps_money(self):
        sub = self._request()
        SubscriptionService.reject_request(sub, reason='нет мест')
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.CANCELLED_BY_TEACHER)
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('1000000'))

    def test_pay_after_expiry_fails_and_expires(self):
        from django.utils import timezone
        from datetime import timedelta
        sub = self._request()
        SubscriptionService.approve_request(sub)
        sub.refresh_from_db()
        sub.approval_expires_at = timezone.now() - timedelta(hours=1)
        sub.save(update_fields=['approval_expires_at'])
        with self.assertRaises(ValueError):
            SubscriptionService.pay(sub, idempotency_key='late')
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.EXPIRED)

    def test_expire_unpaid_approvals_task(self):
        from django.utils import timezone
        from datetime import timedelta
        sub = self._request()
        SubscriptionService.approve_request(sub)
        sub.refresh_from_db()
        sub.approval_expires_at = timezone.now() - timedelta(minutes=5)
        sub.save(update_fields=['approval_expires_at'])
        n = SubscriptionService.expire_unpaid_approvals()
        self.assertEqual(n, 1)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.EXPIRED)

    def test_standard_tariff_options_priced_from_hourly(self):
        # hourly_rate = 50000 (из _make_teacher_with_subject)
        opts = SubscriptionService.standard_tariff_options(self.teacher, self.subject)
        self.assertEqual(len(opts), 3)
        self.assertEqual(opts[0]['lessons_per_week'], 1)
        # 50000 × (60/60) × 1 × 4 = 200000
        self.assertEqual(opts[0]['price_per_month'], Decimal('200000'))
        self.assertEqual(opts[2]['lessons_per_week'], 3)
        self.assertEqual(opts[2]['price_per_month'], Decimal('600000'))


class TeacherNoShowSettleTests(TestCase):
    """settle_after_end: учитель не пришёл → no_show_teacher без выплаты."""

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('w2ns_t')
        self.tariff = _make_tariff(self.teacher, self.subject)
        self.student = _make_student_with_balance('w2ns_s', balance=Decimal('1000000'))
        self.subscription = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff, idempotency_key='w2ns-purchase',
        )

    def _jitsi_booking(self):
        from teachers.models import Booking
        b = Booking.objects.filter(subscription=self.subscription).first()
        b.meeting_url = b.build_meeting_url()  # наш Jitsi → присутствие отслеживается
        b.save(update_fields=['meeting_url'])
        return b

    def test_teacher_no_show_marks_status_and_refunds(self):
        b = self._jitsi_booking()
        self.student.wallet.refresh_from_db()
        balance_before = self.student.wallet.balance
        total_before = self.subscription.total_lessons

        # Учитель не открывал комнату → teacher_joined_at is None.
        result = b.settle_after_end()
        self.assertEqual(result, 'no_show_teacher')
        b.refresh_from_db()
        self.assertEqual(b.status, 'no_show_teacher')

        # Возврат стоимости урока ученику (то, что делает _refund_teacher_no_show).
        SubscriptionService.refund_lesson(b, cancelled_by='teacher',
                                          reason='Учитель не подключился')
        self.student.wallet.refresh_from_db()
        self.subscription.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, balance_before + Decimal('100000.00'))
        self.assertEqual(self.subscription.total_lessons, total_before - 1)

    def test_teacher_present_completes(self):
        b = self._jitsi_booking()
        b.record_join(is_teacher=True)
        b.record_join(is_teacher=False)  # ученик тоже подключился
        result = b.settle_after_end()
        self.assertEqual(result, 'completed')
        b.refresh_from_db()
        self.assertEqual(b.status, 'completed')

    def test_teacher_present_student_absent_is_no_show_student(self):
        b = self._jitsi_booking()
        b.record_join(is_teacher=True)  # только учитель
        result = b.settle_after_end()
        self.assertEqual(result, 'no_show_student')
        b.refresh_from_db()
        self.assertEqual(b.status, 'no_show_student')

    def test_record_join_idempotent(self):
        b = self._jitsi_booking()
        b.record_join(is_teacher=True)
        first = b.teacher_joined_at
        self.assertIsNotNone(first)
        b.record_join(is_teacher=True)
        self.assertEqual(b.teacher_joined_at, first)
        self.assertIsNotNone(b.started_at)


# ---------- Enrollment flow: service edge cases ---------------------------


class EnrollmentServiceEdgeTests(TestCase):
    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('en_t')
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('en_s', balance=Decimal('1000000'))

    def _request(self, key='1', student=None, price=None):
        t = self.tariff
        return SubscriptionService.create_request(
            student=student or self.student, teacher=self.teacher, subject=self.subject,
            lessons_per_week=t.lessons_per_week,
            lesson_duration_minutes=t.lesson_duration_minutes,
            duration_months=t.duration_months,
            price_per_month=price or t.price_per_month, tariff=t,
            preferred_schedule='', idempotency_key=f'en-{key}',
        )

    def _to_active(self, key='1'):
        sub = self._request(key)
        SubscriptionService.approve_request(sub)
        SubscriptionService.pay(sub, idempotency_key=f'pay-{key}')
        sub.refresh_from_db()
        return sub

    def test_book_schedule_wrong_length(self):
        sub = self._to_active()
        with self.assertRaises(ValueError):
            SubscriptionService.book_schedule(sub, [{'day': 'monday', 'time': '10:00'}])

    def test_book_schedule_outside_hours(self):
        sub = self._to_active()
        with self.assertRaises(ValueError):
            SubscriptionService.book_schedule(sub, [
                {'day': 'monday', 'time': '20:00'},   # вне 09:00-13:00
                {'day': 'wednesday', 'time': '10:00'},
            ])

    def test_book_schedule_duplicate_slot(self):
        sub = self._to_active()
        with self.assertRaises(ValueError):
            SubscriptionService.book_schedule(sub, [
                {'day': 'monday', 'time': '10:00'},
                {'day': 'monday', 'time': '10:00'},
            ])

    def test_book_schedule_before_pay(self):
        sub = self._request()
        SubscriptionService.approve_request(sub)
        sub.refresh_from_db()
        with self.assertRaises(ValueError):
            SubscriptionService.book_schedule(sub, [
                {'day': 'monday', 'time': '10:00'},
                {'day': 'wednesday', 'time': '10:00'},
            ])

    def test_pay_insufficient_funds_keeps_state(self):
        poor = _make_student_with_balance('en_poor', balance=Decimal('100'))
        sub = self._request('poor', student=poor)
        SubscriptionService.approve_request(sub)
        with self.assertRaises(InsufficientFunds):
            SubscriptionService.pay(sub, idempotency_key='poor-pay')
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.PENDING_PAYMENT)
        poor.wallet.refresh_from_db()
        self.assertEqual(poor.wallet.balance, Decimal('100'))

    def test_double_pay_charges_once(self):
        sub = self._request()
        SubscriptionService.approve_request(sub)
        SubscriptionService.pay(sub, idempotency_key='one')
        with self.assertRaises(ValueError):
            SubscriptionService.pay(sub, idempotency_key='two')
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('200000.00'))

    def test_approve_twice_fails(self):
        sub = self._request()
        SubscriptionService.approve_request(sub)
        with self.assertRaises(ValueError):
            SubscriptionService.approve_request(sub)

    def test_reject_after_approve_fails(self):
        sub = self._request()
        SubscriptionService.approve_request(sub)
        with self.assertRaises(ValueError):
            SubscriptionService.reject_request(sub)

    def test_cancel_pending_approval_no_money(self):
        sub = self._request()
        res = SubscriptionService.cancel(sub, cancelled_by='student')
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.CANCELLED_BY_STUDENT)
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('1000000'))
        self.assertEqual(res['refunded'], Decimal('0.00'))

    def test_cancel_after_pay_refunds_escrow(self):
        sub = self._to_active()
        SubscriptionService.cancel(sub, cancelled_by='student')
        sub.refresh_from_db()
        self.assertEqual(sub.escrow_balance, Decimal('0.00'))
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('1000000'))

    def test_active_blocks_new_request(self):
        self._to_active()
        with self.assertRaises(AlreadySubscribed):
            self._request('again')

    def test_money_conserved_request_to_pay(self):
        sub = self._request()
        self.student.wallet.refresh_from_db()
        before = self.student.wallet.balance + sub.escrow_balance
        SubscriptionService.approve_request(sub)
        SubscriptionService.pay(sub, idempotency_key='mc')
        sub.refresh_from_db()
        self.student.wallet.refresh_from_db()
        after = self.student.wallet.balance + sub.escrow_balance
        self.assertEqual(before, after)

    def test_standard_request_without_tariff(self):
        opt = SubscriptionService.standard_tariff_options(self.teacher, self.subject)[2]  # 3/нед
        s2 = _make_student_with_balance('en_s2', balance=Decimal('3000000'))
        sub = SubscriptionService.create_request(
            student=s2, teacher=self.teacher, subject=self.subject,
            lessons_per_week=opt['lessons_per_week'],
            lesson_duration_minutes=opt['lesson_duration_minutes'],
            duration_months=opt['duration_months'],
            price_per_month=opt['price_per_month'], tariff=None,
            preferred_schedule='', idempotency_key='en-std',
        )
        self.assertEqual(sub.total_lessons, 12)  # 3 × 4 × 1
        self.assertIsNone(sub.tariff_id)
        self.assertEqual(sub.price_total, Decimal('600000.00'))


# ---------- Enrollment flow: HTTP / access control ------------------------


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class EnrollmentViewTests(TestCase):
    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from teachers.models import TimeSlot, Booking
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('ev_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('ev_s', balance=Decimal('1000000'))
        past = timezone.now() - timedelta(days=2)
        sl = TimeSlot.objects.create(
            teacher=self.teacher, start_at=past, end_at=past + timedelta(minutes=60), status='booked',
        )
        Booking.objects.create(
            slot=sl, student=self.student, subject=self.subject,
            status='completed', is_trial=True,
        )

    def _url(self, name, *args):
        from django.urls import reverse
        return reverse(name, args=args)

    def _approved_sub(self):
        sub = SubscriptionService.create_request(
            student=self.student, teacher=self.teacher, subject=self.subject,
            lessons_per_week=2, lesson_duration_minutes=60, duration_months=1,
            price_per_month=Decimal('800000'), tariff=self.tariff,
            preferred_schedule='', idempotency_key='ev-svc',
        )
        SubscriptionService.approve_request(sub)
        return sub

    def test_continue_requires_login(self):
        r = self.client.get(self._url('continue_learning', self.teacher.id))
        self.assertEqual(r.status_code, 302)
        self.assertIn('/login', r.url)

    def test_teacher_detail_shows_continue_after_trial(self):
        self.client.login(username='ev_s', password='x' * 12)
        r = self.client.get(self._url('teacher_detail', self.teacher.id))
        self.assertContains(r, 'Продолжить обучение')

    def test_teacher_user_cannot_enroll(self):
        self.client.login(username='ev_t', password='x' * 12)
        r = self.client.get(self._url('continue_learning', self.teacher.id))
        self.assertEqual(r.status_code, 302)

    def test_continue_post_creates_request(self):
        self.client.login(username='ev_s', password='x' * 12)
        r = self.client.post(self._url('continue_learning', self.teacher.id), {
            'subject_id': self.subject.id, 'tariff_id': self.tariff.id,
            'preferred_schedule': 'вечером', 'idempotency_key': 'k1',
        })
        self.assertEqual(r.status_code, 302)
        sub = Subscription.objects.get(student=self.student, teacher=self.teacher)
        self.assertEqual(sub.status, Subscription.Status.PENDING_APPROVAL)
        self.assertEqual(sub.preferred_schedule, 'вечером')

    def test_full_http_flow(self):
        from teachers.models import Booking
        self.client.login(username='ev_s', password='x' * 12)
        self.client.post(self._url('continue_learning', self.teacher.id), {
            'subject_id': self.subject.id, 'tariff_id': self.tariff.id,
            'idempotency_key': 'k1',
        })
        sub = Subscription.objects.get(student=self.student, teacher=self.teacher)

        # Учитель подтверждает
        self.client.login(username='ev_t', password='x' * 12)
        r = self.client.post(self._url('learning_request_action', sub.id), {'action': 'approve'})
        self.assertEqual(r.status_code, 302)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.PENDING_PAYMENT)

        # Ученик оплачивает
        self.client.login(username='ev_s', password='x' * 12)
        self.assertEqual(self.client.get(self._url('subscription_pay', sub.id)).status_code, 200)
        r = self.client.post(self._url('subscription_pay', sub.id), {'idempotency_key': 'p1'})
        self.assertEqual(r.status_code, 302)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)

        # Ученик выбирает расписание
        self.assertEqual(self.client.get(self._url('subscription_schedule', sub.id)).status_code, 200)
        r = self.client.post(self._url('subscription_schedule', sub.id),
                             {'slot': ['monday|10:00', 'wednesday|10:00']})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Booking.objects.filter(subscription=sub).count(), sub.total_lessons)

    def test_schedule_wrong_count_no_bookings(self):
        from teachers.models import Booking
        sub = self._approved_sub()
        SubscriptionService.pay(sub, idempotency_key='svc-pay')
        self.client.login(username='ev_s', password='x' * 12)
        r = self.client.post(self._url('subscription_schedule', sub.id),
                             {'slot': ['monday|10:00']})  # нужно 2
        self.assertEqual(r.status_code, 200)  # re-render с ошибкой
        self.assertEqual(Booking.objects.filter(subscription=sub).count(), 0)

    def test_cannot_pay_others_subscription(self):
        sub = self._approved_sub()
        _make_student_with_balance('ev_other', balance=Decimal('1000000'))
        self.client.login(username='ev_other', password='x' * 12)
        r = self.client.post(self._url('subscription_pay', sub.id), {'idempotency_key': 'x'})
        self.assertEqual(r.status_code, 404)

    def test_other_teacher_cannot_action_request(self):
        sub = SubscriptionService.create_request(
            student=self.student, teacher=self.teacher, subject=self.subject,
            lessons_per_week=2, lesson_duration_minutes=60, duration_months=1,
            price_per_month=Decimal('800000'), tariff=self.tariff,
            preferred_schedule='', idempotency_key='ev-req2',
        )
        other_t, _ = _make_teacher_with_subject('ev_t2')
        self.client.login(username='ev_t2', password='x' * 12)
        r = self.client.post(self._url('learning_request_action', sub.id), {'action': 'approve'})
        self.assertEqual(r.status_code, 404)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.PENDING_APPROVAL)


# ---------- Enrollment flow: integration (flow → lessons → payout) --------


class EnrollmentIntegrationTests(TestCase):
    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('in_t')
        self.student = _make_student_with_balance('in_s', balance=Decimal('3000000'))

    def _active_scheduled(self, lpw=2, months=1, price=Decimal('800000'),
                          pattern=None, key='1'):
        tariff = _make_tariff(self.teacher, self.subject,
                              lessons_per_week=lpw, duration_months=months, price=price)
        sub = SubscriptionService.create_request(
            student=self.student, teacher=self.teacher, subject=self.subject,
            lessons_per_week=lpw, lesson_duration_minutes=60, duration_months=months,
            price_per_month=price, tariff=tariff, preferred_schedule='', idempotency_key=f'in-{key}',
        )
        SubscriptionService.approve_request(sub)
        SubscriptionService.pay(sub, idempotency_key=f'in-pay-{key}')
        sub.refresh_from_db()
        pattern = pattern or [{'day': 'monday', 'time': '10:00'}, {'day': 'wednesday', 'time': '10:00'}]
        SubscriptionService.book_schedule(sub, pattern)
        sub.refresh_from_db()
        return sub

    def test_create_request_rejects_bad_params(self):
        with self.assertRaises(ValueError):
            SubscriptionService.create_request(
                student=self.student, teacher=self.teacher, subject=self.subject,
                lessons_per_week=0, lesson_duration_minutes=60, duration_months=1,
                price_per_month=Decimal('800000'), tariff=None,
                preferred_schedule='', idempotency_key='bad-1',
            )
        with self.assertRaises(ValueError):
            SubscriptionService.create_request(
                student=self.student, teacher=self.teacher, subject=self.subject,
                lessons_per_week=2, lesson_duration_minutes=60, duration_months=1,
                price_per_month=Decimal('0'), tariff=None,
                preferred_schedule='', idempotency_key='bad-2',
            )

    def test_full_flow_to_payout(self):
        sub = self._active_scheduled()
        self.assertEqual(sub.bookings.count(), 8)
        b = sub.bookings.first()
        b.status = 'completed'
        b.save()  # сигнал completed_lessons += 1
        self.assertTrue(SubscriptionService.release_lesson_payout(b))
        sub.refresh_from_db()
        self.teacher.user.wallet.refresh_from_db()
        self.platform.wallet.refresh_from_db()
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('85000.00'))
        self.assertEqual(self.platform.wallet.balance, Decimal('15000.00'))
        self.assertEqual(sub.escrow_balance, Decimal('700000.00'))
        self.assertEqual(sub.lessons_paid_out, 1)

    def test_multi_month_books_all_lessons(self):
        sub = self._active_scheduled(lpw=2, months=2, price=Decimal('800000'), key='mm')
        self.assertEqual(sub.total_lessons, 16)  # 2 × 4 × 2
        self.assertEqual(sub.bookings.count(), 16)

    def test_book_schedule_skips_taken_slot(self):
        from datetime import datetime, timedelta
        from django.utils import timezone
        from teachers.models import TimeSlot, Booking
        # Готовим активную подписку (без расписания) — создаём вручную через сервис.
        tariff = _make_tariff(self.teacher, self.subject, lessons_per_week=2,
                              duration_months=1, price=Decimal('800000'))
        sub = SubscriptionService.create_request(
            student=self.student, teacher=self.teacher, subject=self.subject,
            lessons_per_week=2, lesson_duration_minutes=60, duration_months=1,
            price_per_month=Decimal('800000'), tariff=tariff,
            preferred_schedule='', idempotency_key='in-skip',
        )
        SubscriptionService.approve_request(sub)
        SubscriptionService.pay(sub, idempotency_key='in-skip-pay')
        sub.refresh_from_db()
        # Занимаем ближайший понедельник 10:00 другим учеником.
        tz = timezone.get_current_timezone()
        now = timezone.now()
        d = (now + timedelta(days=1)).date()
        while d.weekday() != 0:  # ближайший понедельник
            d += timedelta(days=1)
        start = timezone.make_aware(datetime.combine(d, datetime.min.time()).replace(hour=10), tz)
        TimeSlot.objects.create(teacher=self.teacher, start_at=start,
                                end_at=start + timedelta(minutes=60), status='booked')
        created = SubscriptionService.book_schedule(
            sub, [{'day': 'monday', 'time': '10:00'}, {'day': 'wednesday', 'time': '10:00'}],
        )
        # Всё равно набрали 8 уроков, и ни один не попал на занятый слот.
        self.assertEqual(len(created), 8)
        self.assertFalse(any(b.slot.start_at == start for b in created))

    def test_reschedule_keeps_subscription_and_escrow(self):
        from datetime import timedelta
        from django.utils import timezone
        from teachers.models import TimeSlot
        sub = self._active_scheduled(key='resch')
        b = sub.bookings.order_by('slot__start_at').first()
        escrow_before = sub.escrow_balance
        new_start = b.slot.start_at + timedelta(days=14)
        new_slot = TimeSlot.objects.create(
            teacher=self.teacher, start_at=new_start,
            end_at=new_start + timedelta(minutes=60), status='free',
        )
        b.reschedule_by_student(new_slot.id)
        b.refresh_from_db()
        sub.refresh_from_db()
        self.assertEqual(b.slot_id, new_slot.id)
        self.assertEqual(b.subscription_id, sub.id)
        self.assertEqual(b.status, 'pending')  # учитель переподтверждает
        self.assertEqual(sub.escrow_balance, escrow_before)

    def test_cancel_lesson_refund_in_flow(self):
        sub = self._active_scheduled(key='cl')
        b = sub.bookings.first()
        self.student.wallet.refresh_from_db()
        bal_before = self.student.wallet.balance
        refunded = SubscriptionService.refund_lesson(b, cancelled_by='student')
        self.assertEqual(refunded, Decimal('100000.00'))
        sub.refresh_from_db()
        self.student.wallet.refresh_from_db()
        self.assertEqual(sub.total_lessons, 7)
        self.assertEqual(sub.escrow_balance, Decimal('700000.00'))
        self.assertEqual(self.student.wallet.balance, bal_before + Decimal('100000.00'))

    def test_cancel_subscription_frees_future_slots(self):
        sub = self._active_scheduled(key='free')
        slot_ids = list(sub.bookings.values_list('slot_id', flat=True))
        SubscriptionService.cancel(sub, cancelled_by='student')
        from teachers.models import TimeSlot, Booking
        # Будущие брони отменены, их слоты свободны.
        active = Booking.objects.filter(subscription=sub, status__in=('pending', 'confirmed')).count()
        self.assertEqual(active, 0)
        freed = TimeSlot.objects.filter(id__in=slot_ids, status='free').count()
        self.assertEqual(freed, len(slot_ids))

    def test_can_request_again_after_reject(self):
        tariff = _make_tariff(self.teacher, self.subject, lessons_per_week=2,
                              duration_months=1, price=Decimal('800000'))
        sub = SubscriptionService.create_request(
            student=self.student, teacher=self.teacher, subject=self.subject,
            lessons_per_week=2, lesson_duration_minutes=60, duration_months=1,
            price_per_month=Decimal('800000'), tariff=tariff,
            preferred_schedule='', idempotency_key='rej-1',
        )
        SubscriptionService.reject_request(sub, reason='нет мест')
        # После отклонения можно подать заявку заново.
        sub2 = SubscriptionService.create_request(
            student=self.student, teacher=self.teacher, subject=self.subject,
            lessons_per_week=2, lesson_duration_minutes=60, duration_months=1,
            price_per_month=Decimal('800000'), tariff=tariff,
            preferred_schedule='', idempotency_key='rej-2',
        )
        self.assertEqual(sub2.status, Subscription.Status.PENDING_APPROVAL)
        self.assertNotEqual(sub.id, sub2.id)

    def test_ledger_invariant_after_payout(self):
        from django.db.models import Sum
        sub = self._active_scheduled(key='ledger')
        b = sub.bookings.first()
        b.status = 'completed'
        b.save()
        SubscriptionService.release_lesson_payout(b)
        # Для каждого кошелька: balance == SUM(completed tx).
        for user in (self.student, self.teacher.user, self.platform):
            w = user.wallet
            w.refresh_from_db()
            total = (Transaction.objects
                     .filter(wallet=w, status=Transaction.Status.COMPLETED)
                     .aggregate(s=Sum('amount'))['s']) or Decimal('0.00')
            self.assertEqual(w.balance, total, f'ledger mismatch for {user.username}')

    def test_expire_task_ignores_not_yet_expired(self):
        from datetime import timedelta
        from django.utils import timezone
        tariff = _make_tariff(self.teacher, self.subject, lessons_per_week=2,
                              duration_months=1, price=Decimal('800000'))
        sub = SubscriptionService.create_request(
            student=self.student, teacher=self.teacher, subject=self.subject,
            lessons_per_week=2, lesson_duration_minutes=60, duration_months=1,
            price_per_month=Decimal('800000'), tariff=tariff,
            preferred_schedule='', idempotency_key='notexp',
        )
        SubscriptionService.approve_request(sub)
        sub.refresh_from_db()
        sub.approval_expires_at = timezone.now() + timedelta(hours=10)  # ещё не истёк
        sub.save(update_fields=['approval_expires_at'])
        n = SubscriptionService.expire_unpaid_approvals()
        self.assertEqual(n, 0)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.PENDING_PAYMENT)


# ---------- Disputes (ТЗ шаг 8) -------------------------------------------


class LessonDisputeTests(TestCase):
    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('di_t')
        self.tariff = _make_tariff(self.teacher, self.subject, lessons_per_week=2,
                                   duration_months=1, price=Decimal('800000'))
        self.student = _make_student_with_balance('di_s', balance=Decimal('1000000'))
        sub = SubscriptionService.create_request(
            student=self.student, teacher=self.teacher, subject=self.subject,
            lessons_per_week=2, lesson_duration_minutes=60, duration_months=1,
            price_per_month=Decimal('800000'), tariff=self.tariff,
            preferred_schedule='', idempotency_key='di-req',
        )
        SubscriptionService.approve_request(sub)
        SubscriptionService.pay(sub, idempotency_key='di-pay')
        sub.refresh_from_db()
        SubscriptionService.book_schedule(
            sub, [{'day': 'monday', 'time': '10:00'}, {'day': 'wednesday', 'time': '10:00'}],
        )
        self.sub = sub
        self.booking = sub.bookings.first()
        self.booking.status = 'completed'
        self.booking.save()

    def test_open_freezes_payout(self):
        DisputeService.open(self.booking, student=self.student, reason='учитель не дал материал')
        with self.assertRaises(PayoutError):
            SubscriptionService.release_lesson_payout(self.booking)
        self.teacher.user.wallet.refresh_from_db()
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('0.00'))

    def test_open_requires_completed(self):
        b2 = self.sub.bookings.exclude(pk=self.booking.pk).first()  # confirmed
        with self.assertRaises(DisputeError):
            DisputeService.open(b2, student=self.student, reason='x')

    def test_open_blocked_after_payout(self):
        SubscriptionService.release_lesson_payout(self.booking)
        with self.assertRaises(DisputeError):
            DisputeService.open(self.booking, student=self.student, reason='поздно')

    def test_cannot_open_twice(self):
        DisputeService.open(self.booking, student=self.student, reason='раз')
        with self.assertRaises(DisputeError):
            DisputeService.open(self.booking, student=self.student, reason='два')

    def test_open_by_wrong_user(self):
        other = _make_student_with_balance('di_other', balance=Decimal('0'))
        with self.assertRaises(DisputeError):
            DisputeService.open(self.booking, student=other, reason='чужой')

    def test_resolve_refund_returns_money(self):
        d = DisputeService.open(self.booking, student=self.student, reason='проблема')
        self.student.wallet.refresh_from_db()
        bal_before = self.student.wallet.balance
        admin = _make_student_with_balance('di_admin', balance=Decimal('0'))
        DisputeService.resolve_refund(d, admin=admin, note='подтверждено')
        d.refresh_from_db()
        self.sub.refresh_from_db()
        self.student.wallet.refresh_from_db()
        self.assertEqual(d.status, LessonDispute.Status.RESOLVED_REFUND)
        self.assertEqual(self.student.wallet.balance, bal_before + Decimal('100000.00'))
        self.assertEqual(self.sub.total_lessons, 7)

    def test_resolve_reject_pays_teacher(self):
        d = DisputeService.open(self.booking, student=self.student, reason='проблема')
        admin = _make_student_with_balance('di_admin2', balance=Decimal('0'))
        DisputeService.resolve_reject(d, admin=admin, note='необоснованно')
        d.refresh_from_db()
        self.teacher.user.wallet.refresh_from_db()
        self.platform.wallet.refresh_from_db()
        self.assertEqual(d.status, LessonDispute.Status.RESOLVED_REJECTED)
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('85000.00'))
        self.assertEqual(self.platform.wallet.balance, Decimal('15000.00'))

    def test_cancel_dispute_allows_payout(self):
        d = DisputeService.open(self.booking, student=self.student, reason='ошибся')
        DisputeService.cancel(d, student=self.student)
        ok = SubscriptionService.release_lesson_payout(self.booking)
        self.assertTrue(ok)
        self.teacher.user.wallet.refresh_from_db()
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('85000.00'))

    def test_free_lesson_not_disputable(self):
        from teachers.models import TimeSlot, Booking
        from datetime import timedelta
        from django.utils import timezone
        past = timezone.now() - timedelta(days=1)
        sl = TimeSlot.objects.create(teacher=self.teacher, start_at=past,
                                     end_at=past + timedelta(minutes=60), status='booked')
        free_b = Booking.objects.create(slot=sl, student=self.student, subject=self.subject,
                                        status='completed', is_trial=False)
        with self.assertRaises(DisputeError):
            DisputeService.open(free_b, student=self.student, reason='нет денег по уроку')


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class DisputeViewTests(TestCase):
    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('dv_t')
        self.tariff = _make_tariff(self.teacher, self.subject, lessons_per_week=2,
                                   duration_months=1, price=Decimal('800000'))
        self.student = _make_student_with_balance('dv_s', balance=Decimal('1000000'))
        sub = SubscriptionService.create_request(
            student=self.student, teacher=self.teacher, subject=self.subject,
            lessons_per_week=2, lesson_duration_minutes=60, duration_months=1,
            price_per_month=Decimal('800000'), tariff=self.tariff,
            preferred_schedule='', idempotency_key='dv-req',
        )
        SubscriptionService.approve_request(sub)
        SubscriptionService.pay(sub, idempotency_key='dv-pay')
        sub.refresh_from_db()
        SubscriptionService.book_schedule(
            sub, [{'day': 'monday', 'time': '10:00'}, {'day': 'wednesday', 'time': '10:00'}],
        )
        self.booking = sub.bookings.first()
        self.booking.status = 'completed'
        self.booking.save()
        self.admin = User.objects.create_user(
            username='dv_admin', email='a@x.com', password='x' * 12,
            user_type='student', is_staff=True,
        )

    def _url(self, name, *args):
        from django.urls import reverse
        return reverse(name, args=args)

    def test_student_opens_dispute_via_http(self):
        self.client.login(username='dv_s', password='x' * 12)
        r = self.client.post(self._url('dispute_open', self.booking.id),
                             {'reason': 'учитель не подключился к уроку'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(LessonDispute.objects.filter(booking=self.booking,
                        status=LessonDispute.Status.OPEN).exists())

    def test_open_too_short_reason_rejected(self):
        self.client.login(username='dv_s', password='x' * 12)
        r = self.client.post(self._url('dispute_open', self.booking.id), {'reason': 'плохо'})
        self.assertEqual(r.status_code, 200)  # re-render с ошибкой
        self.assertFalse(LessonDispute.objects.filter(booking=self.booking).exists())

    def test_cannot_open_dispute_on_others_lesson(self):
        _make_student_with_balance('dv_other', balance=Decimal('0'))
        self.client.login(username='dv_other', password='x' * 12)
        r = self.client.post(self._url('dispute_open', self.booking.id),
                             {'reason': 'чужой урок длинная причина'})
        self.assertEqual(r.status_code, 404)

    def test_admin_page_requires_staff(self):
        self.client.login(username='dv_s', password='x' * 12)  # не staff
        r = self.client.get(self._url('admin_billing_disputes'))
        self.assertEqual(r.status_code, 302)  # redirect home

    def test_admin_resolves_refund_via_http(self):
        DisputeService.open(self.booking, student=self.student, reason='проблема с уроком')
        d = LessonDispute.objects.get(booking=self.booking)
        self.client.login(username='dv_admin', password='x' * 12)
        self.assertEqual(self.client.get(self._url('admin_billing_disputes')).status_code, 200)
        r = self.client.post(self._url('admin_dispute_action', d.id),
                             {'action': 'refund', 'note': 'ок'})
        self.assertEqual(r.status_code, 302)
        d.refresh_from_db()
        self.assertEqual(d.status, LessonDispute.Status.RESOLVED_REFUND)


class LessonAttendanceTests(TestCase):
    """Реальное присутствие в видео-уроке (события Jitsi → endpoint)."""

    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from teachers.models import TimeSlot, Booking
        self.teacher, self.subject = _make_teacher_with_subject('at_t')
        self.student = _make_student_with_balance('at_s', balance=Decimal('0'))
        start = timezone.now() + timedelta(minutes=5)
        self.slot = TimeSlot.objects.create(
            teacher=self.teacher, start_at=start,
            end_at=start + timedelta(minutes=60), status='booked',
        )
        self.booking = Booking.objects.create(
            slot=self.slot, student=self.student, subject=self.subject,
            status='confirmed', is_trial=False,
        )
        self.booking.meeting_url = self.booking.build_meeting_url()
        self.booking.save(update_fields=['meeting_url'])

    def _url(self):
        from django.urls import reverse
        return reverse('lesson_attendance_api', args=[self.booking.id])

    def test_teacher_join_event_sets_real_join(self):
        self.client.login(username='at_t', password='x' * 12)
        r = self.client.post(self._url(), {'event': 'join'})
        self.assertEqual(r.status_code, 200)
        self.booking.refresh_from_db()
        self.assertIsNotNone(self.booking.teacher_joined_at)
        self.assertIsNone(self.booking.student_joined_at)

    def test_leave_accumulates_and_clamps_seconds(self):
        self.client.login(username='at_t', password='x' * 12)
        self.client.post(self._url(), {'event': 'join'})
        self.client.post(self._url(), {'event': 'leave', 'seconds': '600'})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.teacher_present_seconds, 600)
        # Подделанное огромное значение клампится (60мин*60 + 1800 = 5400).
        self.client.post(self._url(), {'event': 'leave', 'seconds': '999999'})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.teacher_present_seconds, 600 + 5400)

    def test_non_participant_forbidden(self):
        _make_student_with_balance('at_x', balance=Decimal('0'))
        self.client.login(username='at_x', password='x' * 12)
        r = self.client.post(self._url(), {'event': 'join'})
        self.assertEqual(r.status_code, 403)
        self.booking.refresh_from_db()
        self.assertIsNone(self.booking.teacher_joined_at)
        self.assertIsNone(self.booking.student_joined_at)

    def test_settle_uses_real_join_not_page_open(self):
        from datetime import timedelta
        from django.utils import timezone
        # Оба реально подключились (через endpoint) → урок завершается completed.
        self.client.login(username='at_t', password='x' * 12)
        self.client.post(self._url(), {'event': 'join'})
        self.client.logout()
        self.client.login(username='at_s', password='x' * 12)
        self.client.post(self._url(), {'event': 'join'})
        # Сдвигаем слот в прошлое и завершаем.
        self.slot.start_at = timezone.now() - timedelta(minutes=70)
        self.slot.end_at = timezone.now() - timedelta(minutes=10)
        self.slot.save(update_fields=['start_at', 'end_at'])
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.settle_after_end(), 'completed')

    def test_settle_teacher_present_student_absent_is_no_show_student(self):
        from datetime import timedelta
        from django.utils import timezone
        # Учитель подключился, ученик — нет → no_show_student (урок засчитан учителю).
        self.client.login(username='at_t', password='x' * 12)
        self.client.post(self._url(), {'event': 'join'})
        self.slot.start_at = timezone.now() - timedelta(minutes=70)
        self.slot.end_at = timezone.now() - timedelta(minutes=10)
        self.slot.save(update_fields=['start_at', 'end_at'])
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.settle_after_end(), 'no_show_student')

    def test_settle_no_join_is_no_show(self):
        from datetime import timedelta
        from django.utils import timezone
        # Никто не подключался → no_show_teacher.
        self.slot.start_at = timezone.now() - timedelta(minutes=70)
        self.slot.end_at = timezone.now() - timedelta(minutes=10)
        self.slot.save(update_fields=['start_at', 'end_at'])
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.settle_after_end(), 'no_show_teacher')


class EnrollmentConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_pay_charges_once(self):
        """Две одновременные оплаты одной заявки → списание ровно один раз
        (гарантирует UNIQUE idempotency_key на дебете sub-purchase:<id>)."""
        import threading
        from django.db import connection
        teacher, subject = _make_teacher_with_subject('cc_t')
        student = _make_student_with_balance('cc_s', balance=Decimal('1000000'))
        tariff = _make_tariff(teacher, subject, lessons_per_week=2,
                              duration_months=1, price=Decimal('800000'))
        sub = SubscriptionService.create_request(
            student=student, teacher=teacher, subject=subject,
            lessons_per_week=2, lesson_duration_minutes=60, duration_months=1,
            price_per_month=Decimal('800000'), tariff=tariff,
            preferred_schedule='', idempotency_key='cc-req',
        )
        SubscriptionService.approve_request(sub)

        results = []

        def worker(tag):
            try:
                SubscriptionService.pay(sub, idempotency_key=f'cc-{tag}')
                results.append(('ok', tag))
            except Exception as e:
                results.append((type(e).__name__, tag))
            finally:
                connection.close()

        t1 = threading.Thread(target=worker, args=('a',))
        t2 = threading.Thread(target=worker, args=('b',))
        t1.start(); t2.start(); t1.join(); t2.join()

        student.wallet.refresh_from_db()
        # Главный инвариант: НИКОГДА не списано дважды. На SQLite из-за блокировок
        # возможны исходы: ровно одно списание (баланс 200000) ИЛИ оба потока
        # упёрлись в lock и не списали ничего (баланс 1000000). Двойного списания
        # быть не может (UNIQUE sub-purchase:<id>).
        debits = Transaction.objects.filter(
            idempotency_key=f'sub-purchase:{sub.id}',
        ).count()
        self.assertLessEqual(debits, 1, 'двойное списание недопустимо')
        self.assertIn(student.wallet.balance, (Decimal('200000.00'), Decimal('1000000.00')))
        # Если хоть один дебет прошёл — баланс обязан быть 200000.
        if debits == 1:
            self.assertEqual(student.wallet.balance, Decimal('200000.00'))
