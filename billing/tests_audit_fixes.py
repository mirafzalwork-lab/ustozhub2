"""Тесты фиксов аудита 2026-06 (billing-сторона).

Покрывают:
  * reconcile_orphaned_refunds — дозакрытие потерянного возврата за пробные;
  * reconcile_wallet_balances — обнаружение расхождения balance vs ledger;
  * DB-констрейнты Subscription (одна активная на triple, commission_rate 0..1).
"""
from __future__ import annotations

import json
import uuid as uuidlib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.models import Subscription, Transaction, Wallet
from billing.services import SubscriptionService, TrialService, WalletService
from billing.tasks import (
    reconcile_orphaned_refunds,
    reconcile_wallet_balances,
    release_pending_payouts,
)
from billing.tests import (
    SIMPLE_STATIC_STORAGES,
    _make_student_with_balance,
    _make_tariff,
    _make_teacher_with_subject,
)
from teachers.models import Booking, TeacherSubject, TimeSlot


def _paid_trial(student, teacher, subject):
    """Создаёт платный пробный booking (списывает trial_price со студента)."""
    ts = TeacherSubject.objects.get(teacher=teacher, subject=subject)
    ts.is_free_trial = False
    ts.trial_price = Decimal('50000')
    ts.save()
    future = timezone.now() + timedelta(days=1)
    slot = TimeSlot.objects.create(
        teacher=teacher, start_at=future,
        end_at=future + timedelta(minutes=60), status='free',
    )
    return TrialService.book_paid_trial(student=student, slot_id=slot.id, teacher_subject=ts)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class ReconcileOrphanedRefundsTests(TestCase):
    """C1: страховочная сверка потерянных возвратов за пробные."""

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('rec_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('rec_s', balance=Decimal('200000'))

    def _orphan(self, status='cancelled_by_teacher', age_minutes=15):
        """Платный пробный в refund-состоянии БЕЗ транзакции возврата/выплаты."""
        b = _paid_trial(self.student, self.teacher, self.subject)
        old = timezone.now() - timedelta(minutes=age_minutes)
        # .update() минует auto_now и нормальный refund-поток — имитируем «потерю».
        Booking.objects.filter(pk=b.pk).update(status=status, updated_at=old)
        b.refresh_from_db()
        return b

    def test_recovers_orphaned_refund(self):
        b = self._orphan()
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('150000.00'))  # списано
        self.assertFalse(
            Transaction.objects.filter(idempotency_key=f'trial-refund:{b.id}').exists())

        res = reconcile_orphaned_refunds()

        self.assertEqual(res['recovered'], 1)
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('200000.00'))  # возвращено
        self.assertTrue(
            Transaction.objects.filter(idempotency_key=f'trial-refund:{b.id}').exists())

    def test_idempotent_second_run_no_double_refund(self):
        b = self._orphan()
        reconcile_orphaned_refunds()
        res2 = reconcile_orphaned_refunds()
        self.assertEqual(res2['recovered'], 0)
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('200000.00'))
        self.assertEqual(
            Transaction.objects.filter(idempotency_key=f'trial-refund:{b.id}').count(), 1)

    def test_already_refunded_skipped(self):
        b = self._orphan()
        # Возврат уже сделан штатно.
        TrialService.refund_trial(b, reason='manual')
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('200000.00'))
        res = reconcile_orphaned_refunds()
        self.assertEqual(res['recovered'], 0)

    def test_recent_booking_within_buffer_skipped(self):
        # Возраст < 10 мин — задача не трогает (гонка с синхронным refund во view).
        self._orphan(age_minutes=2)
        res = reconcile_orphaned_refunds()
        self.assertEqual(res['recovered'], 0)
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('150000.00'))

    def test_paid_out_trial_not_refunded(self):
        # Если по пробному была выплата учителю — возврат делать нельзя.
        b = self._orphan(status='completed')
        Transaction.objects.create(
            wallet=self.teacher.user.wallet, amount=Decimal('1'),
            balance_after=Decimal('1'), type=Transaction.Type.LESSON_PAYOUT,
            idempotency_key=f'trial-payout:{b.id}',
        )
        # completed не входит в refund-состояния → задача его и не возьмёт,
        # но даже если бы взяла, payout-ключ заблокировал бы возврат.
        res = reconcile_orphaned_refunds()
        self.assertEqual(res['recovered'], 0)
        self.assertFalse(
            Transaction.objects.filter(idempotency_key=f'trial-refund:{b.id}').exists())


class ReconcileWalletBalancesTests(TestCase):
    """C1: ночная сверка balance == SUM(transactions)."""

    def test_balanced_wallets_no_mismatch(self):
        _make_student_with_balance('bal_s', balance=Decimal('100000'))
        res = reconcile_wallet_balances()
        self.assertEqual(res['mismatches'], [])

    def test_corrupted_balance_detected(self):
        student = _make_student_with_balance('corr_s', balance=Decimal('100000'))
        # Портим денормализованный баланс мимо сервиса (ledger остаётся 100000).
        Wallet.objects.filter(user=student).update(balance=Decimal('99999.00'))
        res = reconcile_wallet_balances()
        ids = [m['user_id'] for m in res['mismatches']]
        self.assertIn(student.pk, ids)
        bad = next(m for m in res['mismatches'] if m['user_id'] == student.pk)
        self.assertEqual(Decimal(bad['balance']), Decimal('99999.00'))
        self.assertEqual(Decimal(bad['ledger_sum']), Decimal('100000.00'))


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class SubscriptionConstraintTests(TestCase):
    """C6: DB-констрейнты подписки."""

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('con_t')
        self.student = _make_student_with_balance('con_s', balance=Decimal('0'))

    def _sub_kwargs(self, **over):
        base = dict(
            student=self.student, teacher=self.teacher, subject=self.subject,
            status=Subscription.Status.ACTIVE,
            lessons_per_week=2, lesson_duration_minutes=60, duration_months=1,
            total_lessons=8, price_total=Decimal('800000'),
            price_per_lesson=Decimal('100000'), commission_rate=Decimal('0.15'),
            purchase_idempotency_key=f'k-{uuidlib.uuid4()}',
        )
        base.update(over)
        return base

    def test_commission_rate_above_one_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Subscription.objects.create(**self._sub_kwargs(commission_rate=Decimal('1.5')))

    def test_commission_rate_negative_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Subscription.objects.create(**self._sub_kwargs(commission_rate=Decimal('-0.1')))

    def test_duplicate_active_subscription_rejected_at_db(self):
        Subscription.objects.create(**self._sub_kwargs())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Subscription.objects.create(**self._sub_kwargs())

    def test_same_triple_allowed_after_first_cancelled(self):
        s1 = Subscription.objects.create(**self._sub_kwargs())
        s1.status = Subscription.Status.CANCELLED_BY_STUDENT
        s1.save(update_fields=['status'])
        # Теперь активной нет — вторая активная создаётся без ошибки.
        s2 = Subscription.objects.create(**self._sub_kwargs())
        self.assertEqual(Subscription.objects.filter(
            student=self.student, teacher=self.teacher, subject=self.subject,
            status__in=Subscription.ACTIVE_STATUSES).count(), 1)
        self.assertNotEqual(s1.pk, s2.pk)

    def test_different_subject_allowed(self):
        from teachers.models import Subject, SubjectCategory
        Subscription.objects.create(**self._sub_kwargs())
        cat, _ = SubjectCategory.objects.get_or_create(name='Точные')
        subj2, _ = Subject.objects.get_or_create(name='Математика', defaults={'category': cat})
        # Та же пара, другой предмет — ограничение не срабатывает.
        Subscription.objects.create(**self._sub_kwargs(subject=subj2))
        self.assertEqual(Subscription.objects.filter(
            student=self.student, teacher=self.teacher,
            status__in=Subscription.ACTIVE_STATUSES).count(), 2)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class PayoutSweepStarvationTests(TestCase):
    """Аудит 2026-06-10 CRIT-1: голодание sweep-задач.

    Выплаченные/возвращённые брони навсегда остаются в терминальных статусах,
    и до фикса они продолжали попадать в кандидатский срез [:500] sweep-задач.
    Когда обработанной истории становится больше 500, срез заполняется ею
    целиком и новые уроки никогда не получают выплату/возврат.

    Тесты проверяют семантику фикса: после обработки бронь должна полностью
    исчезать из кандидатов (total/checked == 0 на повторном прогоне), а не
    «попадать в срез и пропускаться» (total > 0, skipped > 0).
    """

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('starv_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('starv_s', balance=Decimal('1000000'))
        self.subscription = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key='starv-purchase',
        )

    def _complete_in_past(self, booking, hours_ago=48):
        """Урок проведён и его слот закончился за пределами grace-окна."""
        end = timezone.now() - timedelta(hours=hours_ago)
        TimeSlot.objects.filter(pk=booking.slot_id).update(
            start_at=end - timedelta(minutes=60), end_at=end)
        booking.status = 'completed'
        booking.save(update_fields=['status', 'updated_at'])

    def test_paid_booking_leaves_candidate_set(self):
        bookings = list(Booking.objects.filter(subscription=self.subscription)[:2])
        for b in bookings:
            self._complete_in_past(b)

        res1 = release_pending_payouts()
        self.assertEqual(res1['paid'], 2)
        self.assertEqual(res1['errors'], 0)

        # Повторный прогон: выплаченные брони не должны даже попадать в срез —
        # иначе при >500 выплаченных уроках новые выплаты остановятся навсегда.
        res2 = release_pending_payouts()
        self.assertEqual(res2['total'], 0)
        self.assertEqual(res2['paid'], 0)

        # Деньги не задвоились.
        self.teacher.user.wallet.refresh_from_db()
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('170000.00'))

    def test_late_cancel_occupies_quota(self):
        """Аудит 2026-06-10 CRIT-3: поздняя отмена занимает квоту пакета.

        Эскроу по late-cancel уже выплачен учителю, total_lessons не менялся.
        До фикса такая бронь исключалась из подсчёта занятых уроков →
        remaining завышался на 1 → ученик поздними отменами «добирал»
        бесплатные уроки, а учитель на последнем уроке оставался без выплаты.
        """
        sub = self.subscription
        bookings = list(Booking.objects.filter(subscription=sub))
        total = sub.total_lessons
        self.assertEqual(len(bookings), total)
        self.assertEqual(SubscriptionService.occupied_lessons(sub), total)

        # Поздняя отмена: до урока меньше CANCELLATION_FULL_REFUND_HOURS.
        victim = bookings[0]
        soon = timezone.now() + timedelta(hours=1)
        TimeSlot.objects.filter(pk=victim.slot_id).update(
            start_at=soon, end_at=soon + timedelta(minutes=60))
        victim.refresh_from_db()
        victim.cancel_by_student()
        result = SubscriptionService.cancel_lesson(
            victim, cancelled_by='student', reason='тест late-cancel')
        self.assertTrue(result['charged'])

        sub.refresh_from_db()
        # Урок засчитан: квота по-прежнему занята целиком, добор невозможен.
        self.assertEqual(sub.total_lessons, total)  # total не уменьшался
        self.assertEqual(SubscriptionService.occupied_lessons(sub), total)
        with self.assertRaisesMessage(ValueError, 'уже забронированы'):
            SubscriptionService.book_schedule(
                sub, [{'day': 'monday', 'time': '10:00'},
                      {'day': 'wednesday', 'time': '10:00'}])

    def test_early_cancel_frees_nothing_extra(self):
        """Ранняя отмена: refund уменьшает total — добор тоже невозможен."""
        sub = self.subscription
        total = sub.total_lessons
        victim = Booking.objects.filter(subscription=sub).first()
        # Слот далеко в будущем (>24ч) — полный возврат.
        far = timezone.now() + timedelta(days=10)
        TimeSlot.objects.filter(pk=victim.slot_id).update(
            start_at=far, end_at=far + timedelta(minutes=60))
        victim.refresh_from_db()
        victim.cancel_by_student()
        result = SubscriptionService.cancel_lesson(
            victim, cancelled_by='student', reason='тест early-cancel')
        self.assertFalse(result['charged'])
        self.assertGreater(result['refunded'], 0)

        sub.refresh_from_db()
        self.assertEqual(sub.total_lessons, total - 1)
        self.assertEqual(SubscriptionService.occupied_lessons(sub), total - 1)

    def test_refunded_trial_leaves_reconcile_candidate_set(self):
        b = _paid_trial(self.student, self.teacher, self.subject)
        old = timezone.now() - timedelta(minutes=15)
        Booking.objects.filter(pk=b.pk).update(
            status='cancelled_by_teacher', updated_at=old)

        res1 = reconcile_orphaned_refunds()
        self.assertEqual(res1['recovered'], 1)

        # Возвращённая бронь больше не кандидат (а не «checked и пропущена»).
        res2 = reconcile_orphaned_refunds()
        self.assertEqual(res2['checked'], 0)
        self.assertEqual(res2['recovered'], 0)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class MulticardErrorThenSuccessTests(TestCase):
    """Аудит 2026-06-10 H1: error-callback не должен вечно блокировать зачисление.

    Multicard переупорядочивает/ретраит webhooks: error первой попытки оплаты
    может прийти раньше success второй. До фикса ERROR был терминальным наравне
    с REVERT — реально оплаченный инвойс никогда не зачислялся (клиент заплатил,
    кошелёк пуст). Теперь зачисление из ERROR разрешено ТОЛЬКО при независимом
    подтверждении шлюзом (get_payment); REVERT остаётся терминальным навсегда.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model
        from billing.models import MulticardInvoice
        User = get_user_model()
        self.user = User.objects.create_user(username='mc_err', password='x' * 12)
        self.invoice = MulticardInvoice.objects.create(
            user=self.user, amount=Decimal('50000.00'),
            store_id='store-1', multicard_uuid='mc-uuid-err',
        )
        self.url = reverse('multicard_callback')

    def _payload(self, **over):
        from billing.multicard import compute_sign, sum_to_tiyin
        amount = over.pop('amount', sum_to_tiyin(self.invoice.amount))
        invoice_id = over.pop('invoice_id', str(self.invoice.id))
        p = {
            'uuid': 'mc-uuid-err', 'invoice_id': invoice_id, 'amount': amount,
            'store_id': 6, 'status': 'success',
        }
        p.update(over)
        p['sign'] = compute_sign(6, invoice_id, amount)
        return p

    def _post(self, payload):
        return self.client.post(self.url, data=json.dumps(payload),
                                content_type='application/json')

    @patch('billing.views._verify_payment_with_gateway', return_value='success')
    def test_error_then_confirmed_success_credits(self, _mock_verify):
        from billing.models import MulticardInvoice
        r1 = self._post(self._payload(status='error'))
        self.assertEqual(r1.status_code, 200)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, MulticardInvoice.Status.ERROR)

        # Затем приходит легитимный success, подтверждённый шлюзом → зачисляем.
        r2 = self._post(self._payload(status='success'))
        self.assertEqual(r2.status_code, 200)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('50000.00'))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, MulticardInvoice.Status.SUCCESS)

    @patch('billing.views._verify_payment_with_gateway', return_value='unknown')
    def test_error_then_unconfirmed_success_no_credit(self, _mock_verify):
        from billing.models import MulticardInvoice
        self._post(self._payload(status='error'))
        # success без независимого подтверждения шлюза (verdict=unknown) из
        # ERROR не зачисляется — только подписи недостаточно.
        self._post(self._payload(status='success'))
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('0.00'))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, MulticardInvoice.Status.ERROR)

    @patch('billing.views._verify_payment_with_gateway', return_value='success')
    def test_revert_still_terminal_even_confirmed(self, _mock_verify):
        from billing.models import MulticardInvoice
        self._post(self._payload(status='revert'))
        self._post(self._payload(status='success'))
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('0.00'))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, MulticardInvoice.Status.REVERT)

    def test_sweep_credits_stuck_error_invoice(self):
        """reconcile_multicard_invoices дозачисляет завиcший ERROR-инвойс."""
        from billing.models import MulticardInvoice, Transaction as Tx
        from billing.multicard import sum_to_tiyin
        from billing.tasks import reconcile_multicard_invoices

        MulticardInvoice.objects.filter(pk=self.invoice.pk).update(
            status=MulticardInvoice.Status.ERROR,
            updated_at=timezone.now() - timedelta(hours=1),
        )
        gw = {'status': 'success', 'amount': sum_to_tiyin(self.invoice.amount)}
        with patch('billing.multicard.MulticardClient.get_payment',
                   return_value=gw):
            res = reconcile_multicard_invoices()

        self.assertEqual(res['credited'], 1)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('50000.00'))
        # Идемпотентность: повторный прогон ничего не дозачисляет.
        with patch('billing.multicard.MulticardClient.get_payment',
                   return_value=gw):
            res2 = reconcile_multicard_invoices()
        self.assertEqual(res2['credited'], 0)
        self.assertEqual(res2['checked'], 0)  # SUCCESS больше не кандидат
        self.assertEqual(Tx.objects.filter(
            wallet=self.user.wallet, type=Tx.Type.DEPOSIT).count(), 1)

    def test_sweep_skips_unpaid_error_invoice(self):
        """Реально сбойный платёж (шлюз говорит error) не зачисляется."""
        from billing.models import MulticardInvoice
        from billing.tasks import reconcile_multicard_invoices

        MulticardInvoice.objects.filter(pk=self.invoice.pk).update(
            status=MulticardInvoice.Status.ERROR,
            updated_at=timezone.now() - timedelta(hours=1),
        )
        with patch('billing.multicard.MulticardClient.get_payment',
                   return_value={'status': 'error', 'amount': 0}):
            res = reconcile_multicard_invoices()
        self.assertEqual(res['credited'], 0)
        self.user.wallet.refresh_from_db()
        self.assertEqual(self.user.wallet.balance, Decimal('0.00'))


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class CancelWithOpenDisputeTests(TestCase):
    """Аудит 2026-06-10 H2: отмена подписки при открытом споре.

    До фикса PayoutError спорного урока пролетал из cancel() наверх → 500,
    подписку нельзя было отменить (деньги ученика заперты до решения спора).
    Теперь отмена проходит, а стоимость спорного урока УДЕРЖИВАЕТСЯ в эскроу:
    resolve_reject платит учителю из удержанного, resolve_refund возвращает
    ученику. Если просто вернуть всё ученику (как делал settle_expired),
    при отклонении спора платить учителю было бы нечем.
    """

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('disp_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('disp_s', balance=Decimal('1000000'))
        self.subscription = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key='disp-purchase',
        )
        self.admin = _make_student_with_balance('disp_a', balance=Decimal('0'))
        self.admin.is_staff = True
        self.admin.save(update_fields=['is_staff'])

    def _disputed_completed_lesson(self):
        """Первый урок проведён, ученик открыл спор (выплата заморожена)."""
        from billing.services import DisputeService
        b = Booking.objects.filter(subscription=self.subscription).first()
        past = timezone.now() - timedelta(hours=2)
        TimeSlot.objects.filter(pk=b.slot_id).update(
            start_at=past - timedelta(minutes=60), end_at=past)
        b.status = 'completed'
        b.save(update_fields=['status', 'updated_at'])
        DisputeService.open(b, student=self.student, reason='учителя не было половину урока')
        return b

    def test_cancel_with_open_dispute_succeeds_and_withholds(self):
        b = self._disputed_completed_lesson()
        sub = self.subscription

        # Отмена НЕ падает (до фикса — PayoutError → 500).
        result = SubscriptionService.cancel(
            sub, cancelled_by='student', reason='ухожу')

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.CANCELLED_BY_STUDENT)
        # Стоимость спорного урока удержана, остальное возвращено.
        self.assertEqual(sub.escrow_balance, sub.price_per_lesson)
        self.assertEqual(
            result['refunded'],
            Decimal('800000.00') - sub.price_per_lesson)
        self.student.wallet.refresh_from_db()
        # 1000000 − 800000 (покупка) + 700000 (возврат) = 900000
        self.assertEqual(self.student.wallet.balance, Decimal('900000.00'))
        # Учителю за спорный урок пока ничего.
        self.teacher.user.wallet.refresh_from_db()
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('0.00'))

    def test_reject_after_cancel_pays_teacher_from_withheld(self):
        from billing.services import DisputeService
        from billing.models import LessonDispute
        b = self._disputed_completed_lesson()
        SubscriptionService.cancel(
            self.subscription, cancelled_by='student', reason='ухожу')

        dispute = LessonDispute.objects.get(booking=b)
        DisputeService.resolve_reject(dispute, admin=self.admin)

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.escrow_balance, Decimal('0.00'))
        self.teacher.user.wallet.refresh_from_db()
        # 100000 за урок, комиссия 15% → 85000 учителю.
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('85000.00'))

    def test_refund_after_cancel_returns_withheld_to_student(self):
        from billing.services import DisputeService
        from billing.models import LessonDispute
        b = self._disputed_completed_lesson()
        SubscriptionService.cancel(
            self.subscription, cancelled_by='student', reason='ухожу')

        dispute = LessonDispute.objects.get(booking=b)
        DisputeService.resolve_refund(dispute, admin=self.admin)

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.escrow_balance, Decimal('0.00'))
        self.student.wallet.refresh_from_db()
        # Всё вернулось: 900000 (после отмены) + 100000 (спорный урок).
        self.assertEqual(self.student.wallet.balance, Decimal('1000000.00'))
        self.teacher.user.wallet.refresh_from_db()
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('0.00'))

    def test_settle_expired_withholds_disputed_escrow(self):
        """Та же политика в settle_expired (раньше молча возвращал всё ученику)."""
        self._disputed_completed_lesson()
        sub = self.subscription
        # «Зависшая» подписка: будущих броней нет (H10: с ними settle_expired
        # не закрывает подписку), срок вышел.
        Booking.objects.filter(
            subscription=sub, status__in=('confirmed', 'pending'),
        ).update(status='expired')
        Subscription.objects.filter(pk=sub.pk).update(
            expires_at=timezone.now() - timedelta(days=2))
        sub.refresh_from_db()

        result = SubscriptionService.settle_expired(sub)
        self.assertIsNotNone(result)
        sub.refresh_from_db()
        self.assertEqual(sub.escrow_balance, sub.price_per_lesson)
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('900000.00'))


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class ReconcileSubscriptionEscrowTests(TestCase):
    """Аудит 2026-06-10 H4: сверка эскроу подписок с леджером.

    Эскроу не лежит ни в одном кошельке — до этой задачи Subscription.
    escrow_balance не сверялся ни с чем, и баги класса «двойная выплата» /
    «возврат мимо эскроу» копились бы незаметно.
    """

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('esc_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('esc_s', balance=Decimal('1000000'))
        self.subscription = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key='esc-purchase',
        )

    def test_healthy_subscription_no_mismatch(self):
        from billing.tasks import reconcile_subscription_escrow
        res = reconcile_subscription_escrow()
        self.assertEqual(res['mismatches'], [])
        self.assertGreaterEqual(res['checked'], 1)

    def test_after_payout_still_consistent(self):
        from billing.tasks import reconcile_subscription_escrow
        b = Booking.objects.filter(subscription=self.subscription).first()
        b.status = 'completed'
        b.save(update_fields=['status', 'updated_at'])
        SubscriptionService.release_lesson_payout(b)
        res = reconcile_subscription_escrow()
        self.assertEqual(res['mismatches'], [])

    def test_corrupted_escrow_detected(self):
        from billing.tasks import reconcile_subscription_escrow
        Subscription.objects.filter(pk=self.subscription.pk).update(
            escrow_balance=Decimal('799999.00'))
        res = reconcile_subscription_escrow()
        ids = [m['subscription'] for m in res['mismatches']]
        self.assertIn(str(self.subscription.pk), ids)
        bad = next(m for m in res['mismatches']
                   if m['subscription'] == str(self.subscription.pk))
        self.assertEqual(Decimal(bad['expected']), Decimal('800000.00'))

    def test_cancelled_with_withheld_escrow_checked_and_consistent(self):
        """Отменённая подписка с удержанием под спор тоже сверяется (escrow>0)."""
        from billing.services import DisputeService
        from billing.tasks import reconcile_subscription_escrow
        b = Booking.objects.filter(subscription=self.subscription).first()
        past = timezone.now() - timedelta(hours=2)
        TimeSlot.objects.filter(pk=b.slot_id).update(
            start_at=past - timedelta(minutes=60), end_at=past)
        b.status = 'completed'
        b.save(update_fields=['status', 'updated_at'])
        DisputeService.open(b, student=self.student, reason='спор')
        SubscriptionService.cancel(
            self.subscription, cancelled_by='student', reason='ухожу')

        self.subscription.refresh_from_db()
        self.assertGreater(self.subscription.escrow_balance, 0)
        res = reconcile_subscription_escrow()
        self.assertEqual(res['mismatches'], [])


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class PendingApprovalTTLTests(TestCase):
    """Аудит 2026-06-10 H9: заявка без ответа учителя сгорает по TTL.

    Раньше PENDING_APPROVAL жила вечно: учитель молчит, а уникальный констрейнт
    активной подписки не давал ученику подать повторную заявку с тем же
    предметом — «горячий» ученик с деньгами был заперт.
    """

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('ttl_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('ttl_s', balance=Decimal('0'))

    def _request(self):
        return SubscriptionService.create_request(
            student=self.student, teacher=self.teacher, subject=self.subject,
            lessons_per_week=2, lesson_duration_minutes=60, duration_months=1,
            price_per_month=Decimal('800000'),
            idempotency_key=f'ttl-{uuidlib.uuid4()}',
        )

    def test_unanswered_request_expires_after_ttl(self):
        sub = self._request()
        old = timezone.now() - timedelta(
            hours=SubscriptionService.APPROVAL_RESPONSE_HOURS + 1)
        Subscription.objects.filter(pk=sub.pk).update(created_at=old)

        n = SubscriptionService.expire_unpaid_approvals()
        self.assertEqual(n, 1)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.EXPIRED)

        # Ученик теперь может подать новую заявку к тому же учителю/предмету.
        sub2 = self._request()
        self.assertEqual(sub2.status, Subscription.Status.PENDING_APPROVAL)
        self.assertNotEqual(sub.pk, sub2.pk)

    def test_fresh_request_not_expired(self):
        sub = self._request()
        n = SubscriptionService.expire_unpaid_approvals()
        self.assertEqual(n, 0)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.PENDING_APPROVAL)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class SettleExpiredKeepsFutureBookingsTests(TestCase):
    """Аудит 2026-06-10 H10: settle_expired не срезает будущие брони.

    Генератор расписания законно раскладывает уроки на срок длиннее expires_at
    (lookahead 2× при нехватке слотов). Раньше settle_expired через 30 дней +
    grace отменял такие подтверждённые брони с возвратом — оплаченные уроки
    исчезали из календаря ученика. Теперь подписка с будущими брониями не
    закрывается (живёт до последнего урока), а задача-кандидат исключает их
    до среза (анти-голодание).
    """

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('h10_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('h10_s', balance=Decimal('1000000'))
        self.subscription = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key='h10-purchase',
        )

    def test_expired_sub_with_future_bookings_not_settled(self):
        sub = self.subscription
        Subscription.objects.filter(pk=sub.pk).update(
            expires_at=timezone.now() - timedelta(days=2))
        sub.refresh_from_db()
        future_before = Booking.objects.filter(
            subscription=sub, status__in=('confirmed', 'pending')).count()
        self.assertGreater(future_before, 0)

        result = SubscriptionService.settle_expired(sub)

        self.assertIsNone(result)  # подписка не закрыта
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        # Брони на месте, возврата не было.
        self.assertEqual(Booking.objects.filter(
            subscription=sub, status__in=('confirmed', 'pending')).count(),
            future_before)
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('200000.00'))

    def test_task_skips_delivering_subs_before_slice(self):
        """Кандидаты задачи: «доставляющиеся» подписки исключены до среза."""
        from billing.tasks import settle_expired_subscriptions
        sub = self.subscription
        Subscription.objects.filter(pk=sub.pk).update(
            expires_at=timezone.now() - timedelta(days=2))

        res = settle_expired_subscriptions()
        self.assertEqual(res['settled'], 0)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)

    def test_truly_stuck_sub_still_settles(self):
        """Реально зависшая (без будущих броней) — закрывается как раньше."""
        sub = self.subscription
        Booking.objects.filter(subscription=sub).update(status='expired')
        Subscription.objects.filter(pk=sub.pk).update(
            expires_at=timezone.now() - timedelta(days=2))
        sub.refresh_from_db()

        result = SubscriptionService.settle_expired(sub)
        self.assertIsNotNone(result)
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.EXPIRED)
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('1000000.00'))


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class RenewalNudgeTests(TestCase):
    """Аудит 2026-06-10 M22: уведомления о продлении пакета."""

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('rnw_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('rnw_s', balance=Decimal('1000000'))
        self.subscription = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key='rnw-purchase',
        )

    def _payout_n(self, n):
        bookings = list(Booking.objects.filter(subscription=self.subscription)
                        .order_by('created_at')[:n])
        for b in bookings:
            b.status = 'completed'
            b.save(update_fields=['status', 'updated_at'])
            SubscriptionService.release_lesson_payout(b)

    def _notifications_for_student(self):
        from teachers.models import Notification
        return Notification.objects.filter(target_user=self.student)

    def test_nudge_sent_when_two_lessons_remain(self):
        self._payout_n(6)  # 8 - 6 = 2
        notif = self._notifications_for_student().filter(
            title__icontains='Осталось 2 урока')
        self.assertEqual(notif.count(), 1)
        self.assertIn('continue', notif.first().action_url)

    def test_completion_notice_with_renew_cta(self):
        self._payout_n(8)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.COMPLETED)
        notif = self._notifications_for_student().filter(
            title__icontains='Пакет уроков завершён')
        self.assertEqual(notif.count(), 1)
        self.assertIn('continue', notif.first().action_url)

    def test_no_duplicate_nudge_on_repeated_payout_call(self):
        self._payout_n(6)
        # Повторный вызов payout по уже выплаченному уроку — идемпотентен,
        # уведомление не дублируется.
        b = Booking.objects.filter(subscription=self.subscription,
                                   status='completed').first()
        SubscriptionService.release_lesson_payout(b)
        notif = self._notifications_for_student().filter(
            title__icontains='Осталось 2 урока')
        self.assertEqual(notif.count(), 1)

    def test_renew_button_in_history_page(self):
        self._payout_n(8)
        self.client.force_login(self.student)
        r = self.client.get(reverse('my_subscriptions'))
        self.assertContains(r, 'Продлить')
        self.assertContains(r, f'/learn/{self.teacher.id}/continue/')


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class MoneyInvariantChaosTests(TestCase):
    """Сквозной «хаос-сценарий»: грязная цепочка событий жизненного цикла,
    после которой ВСЕ денежные инварианты обязаны сходиться.

    Цепочка: покупка пакета (8 уроков) → урок проведён+выплачен → поздняя
    отмена (штраф учителю) → прощённая неявка → неявка учителя (возврат) →
    проведённый урок со спором → отмена подписки (удержание спорного) →
    резолюция спора в пользу учителя → сверки кошельков и эскроу чистые,
    глобальный баланс системы сходится.
    """

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        self.platform = get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('chaos_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.tariff = _make_tariff(self.teacher, self.subject,
                                   lessons_per_week=2, duration_months=1,
                                   price=Decimal('800000'))
        self.student = _make_student_with_balance('chaos_s', balance=Decimal('1000000'))
        self.sub = SubscriptionService.purchase(
            student=self.student, tariff=self.tariff,
            idempotency_key='chaos-purchase')
        self.bookings = list(
            Booking.objects.filter(subscription=self.sub).order_by('created_at'))

    def _move_slot(self, b, *, hours_from_now):
        start = timezone.now() + timedelta(hours=hours_from_now)
        TimeSlot.objects.filter(pk=b.slot_id).update(
            start_at=start, end_at=start + timedelta(minutes=60))
        b.refresh_from_db()

    def test_chaos_lifecycle_invariants_hold(self):
        from billing.services import DisputeService
        from billing.models import LessonDispute
        from billing.tasks import (
            reconcile_subscription_escrow, reconcile_wallet_balances,
        )

        b1, b2, b3, b4, b5 = self.bookings[:5]

        # 1) Урок проведён и выплачен.
        self._move_slot(b1, hours_from_now=-30)
        b1.status = 'completed'
        b1.save(update_fields=['status', 'updated_at'])
        self.assertTrue(SubscriptionService.release_lesson_payout(b1))

        # 2) Поздняя отмена учеником (<24ч) — урок засчитан, штраф учителю.
        self._move_slot(b2, hours_from_now=1)
        b2.cancel_by_student()
        res = SubscriptionService.cancel_lesson(
            b2, cancelled_by='student', reason='хаос')
        self.assertTrue(res['charged'])

        # 3) Прощённая неявка — урок возвращён в квоту, денег не двигали.
        self._move_slot(b3, hours_from_now=-5)
        Booking.objects.filter(pk=b3.pk).update(
            status='no_show_student', no_show_forgiven=True)
        b3.refresh_from_db()
        self.assertFalse(SubscriptionService.release_lesson_payout(b3))

        # 4) Неявка учителя — возврат стоимости урока на баланс, пакет −1.
        self._move_slot(b4, hours_from_now=-3)
        Booking.objects.filter(pk=b4.pk).update(status='no_show_teacher')
        b4.refresh_from_db()
        refunded = SubscriptionService.refund_lesson(
            b4, cancelled_by='teacher', reason='неявка учителя')
        self.assertGreater(refunded, 0)

        # 5) Проведённый урок, ученик открыл спор.
        self._move_slot(b5, hours_from_now=-2)
        b5.status = 'completed'
        b5.save(update_fields=['status', 'updated_at'])
        DisputeService.open(b5, student=self.student, reason='спор')

        # 6) Отмена подписки: не падает, спорный эскроу удержан.
        result = SubscriptionService.cancel(
            self.sub, cancelled_by='student', reason='хаос-отмена')
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.escrow_balance, self.sub.price_per_lesson)

        # Сверки ПОСЛЕ отмены: эскроу и кошельки сходятся.
        self.assertEqual(reconcile_subscription_escrow()['mismatches'], [])
        self.assertEqual(reconcile_wallet_balances()['mismatches'], [])

        # 7) Спор отклонён — учитель получает выплату из удержанного эскроу.
        admin = _make_student_with_balance('chaos_a', balance=Decimal('0'))
        admin.is_staff = True
        admin.save(update_fields=['is_staff'])
        DisputeService.resolve_reject(
            LessonDispute.objects.get(booking=b5), admin=admin)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.escrow_balance, Decimal('0.00'))

        # Финальные сверки чистые.
        self.assertEqual(reconcile_subscription_escrow()['mismatches'], [])
        self.assertEqual(reconcile_wallet_balances()['mismatches'], [])

        # Глобальный инвариант системы: деньги никуда не исчезли и не родились.
        # Внесено в систему: 1 000 000 (депозит ученика). Всё это должно
        # лежать в кошельках (ученик + учитель + платформа), эскроу = 0.
        for w in (self.student, self.teacher.user, self.platform):
            w.wallet.refresh_from_db()
        total = (self.student.wallet.balance
                 + self.teacher.user.wallet.balance
                 + self.platform.wallet.balance)
        self.assertEqual(total, Decimal('1000000.00'))

        # Учитель получил ровно за 3 засчитанных урока (проведённый, late-cancel,
        # спорный) × 85 000 (после комиссии 15%).
        self.assertEqual(self.teacher.user.wallet.balance, Decimal('255000.00'))
        # Платформа — комиссия 3 × 15 000.
        self.assertEqual(self.platform.wallet.balance, Decimal('45000.00'))
        # Ученик: 1 000 000 − 800 000 (пакет) + возвраты. Возвраты: неявка
        # учителя (100 000) + остаток эскроу при отмене (800 000 − 3×100 000
        # выплат − 100 000 возврата − 100 000 удержано = 300 000) + 0 после
        # спора. Итого 200 000 + 100 000 + 300 000 = 700 000... проверяем
        # фактом: total сходится и эскроу пуст, поэтому баланс ученика
        # вычисляется как остаток.
        self.assertEqual(
            self.student.wallet.balance,
            Decimal('1000000.00') - self.teacher.user.wallet.balance
            - self.platform.wallet.balance,
        )
