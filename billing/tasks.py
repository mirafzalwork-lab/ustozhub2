"""Celery tasks для биллинга.

release_pending_payouts — каждые 5 мин ищет завершённые subscription-уроки,
у которых истёк PAYOUT_GRACE_HOURS, и выплачивает учителю + комиссию платформе.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name='billing.release_pending_payouts')
def release_pending_payouts():
    """Раз в N минут: выплачиваем учителям за уроки, прошедшие grace window.

    Обрабатываются два потока:
      1) Subscription-уроки (booking.subscription IS NOT NULL).
      2) Платные пробные (booking.is_trial=True, booking.trial_price_paid IS NOT NULL).

    Общие условия:
      * booking.status == 'completed'
      * booking.slot.end_at + PAYOUT_GRACE_HOURS < now
      * ещё нет Transaction для соответствующего payout-ключа.
    """
    from teachers.models import Booking
    from .models import Transaction
    from .services import PayoutError, SubscriptionService, TrialService

    threshold = timezone.now() - timedelta(hours=settings.PAYOUT_GRACE_HOURS)

    paid = 0
    skipped = 0
    errors = 0
    total = 0

    # === Поток 1: подписочные уроки ===
    # Доставленные = completed ИЛИ no_show_student (ученик не пришёл, урок засчитан).
    sub_candidates = (
        Booking.objects
        .filter(status__in=('completed', 'no_show_student'),
                subscription__isnull=False, slot__end_at__lt=threshold)
        .exclude(no_show_forgiven=True)   # прощённая неявка — выплаты нет (ТЗ §6)
        .exclude(dispute__status='open')  # заморозка выплаты на время спора
        .select_related('subscription', 'slot')
        .order_by('slot__end_at')[:500]
    )
    for booking in sub_candidates:
        total += 1
        payout_key = f'lesson-payout:{booking.id}'
        if Transaction.objects.filter(idempotency_key=payout_key).exists():
            skipped += 1
            continue
        try:
            ok = SubscriptionService.release_lesson_payout(booking)
            paid += 1 if ok else 0
            skipped += 0 if ok else 1
        except PayoutError as e:
            errors += 1
            logger.warning(f'subscription payout failed booking={booking.id}: {e}')
        except Exception as e:
            errors += 1
            logger.exception(f'unexpected sub payout error booking={booking.id}: {e}')

    # === Поток 2: платные пробные ===
    trial_candidates = (
        Booking.objects
        .filter(
            status__in=('completed', 'no_show_student'), is_trial=True,
            trial_price_paid__isnull=False,
            slot__end_at__lt=threshold,
        )
        .exclude(dispute__status='open')  # заморозка выплаты на время спора
        .select_related('slot__teacher__user', 'student')
        .order_by('slot__end_at')[:500]
    )
    for booking in trial_candidates:
        total += 1
        payout_key = f'trial-payout:{booking.id}'
        if Transaction.objects.filter(idempotency_key=payout_key).exists():
            skipped += 1
            continue
        try:
            ok = TrialService.release_trial_payout(booking)
            paid += 1 if ok else 0
            skipped += 0 if ok else 1
        except PayoutError as e:
            errors += 1
            logger.warning(f'trial payout failed booking={booking.id}: {e}')
        except Exception as e:
            errors += 1
            logger.exception(f'unexpected trial payout error booking={booking.id}: {e}')

    return {'paid': paid, 'skipped': skipped, 'errors': errors, 'total': total}


@shared_task(name='billing.reconcile_orphaned_refunds')
def reconcile_orphaned_refunds():
    """Страховочная сверка: дозакрывает «потерянные» возвраты за пробные уроки.

    Возврат за платный пробный при отмене/неявке учителя вызывается во view
    (booking_cancel_api) ПОСЛЕ коммита смены статуса — если процесс упадёт между
    этими шагами, урок останется отменённым, а деньги ученику не вернутся, и
    никакой sweep их не подберёт (в отличие от подписок, где escrow страхует
    settle_expired_subscriptions).

    Эта задача ищет платные пробные в refund-состояниях, у которых нет ни
    transaction возврата, ни выплаты учителю, и повторяет refund_trial (он
    идемпотентен по 'trial-refund:<id>'). Берём только брони, не менявшиеся
    последние 10 минут, чтобы не гоняться с синхронным refund во view.
    """
    from teachers.models import Booking
    from .models import Transaction
    from .services import TrialService

    REFUND_STATES = (
        'cancelled_by_student', 'cancelled_by_teacher',
        'no_show_teacher', 'expired', 'not_held',
    )
    buffer = timezone.now() - timedelta(minutes=10)

    candidates = (
        Booking.objects
        .filter(status__in=REFUND_STATES, is_trial=True,
                trial_price_paid__isnull=False, updated_at__lt=buffer)
        .order_by('updated_at')[:500]
    )

    recovered = 0
    checked = 0
    errors = 0
    for booking in candidates:
        checked += 1
        keys = [f'trial-refund:{booking.id}', f'trial-payout:{booking.id}']
        if Transaction.objects.filter(idempotency_key__in=keys).exists():
            continue  # деньги уже двинулись (возврат или выплата) — всё ок
        try:
            refunded = TrialService.refund_trial(
                booking, reason='Авто-сверка потерянного возврата',
            )
            if refunded:
                recovered += 1
                logger.warning(
                    'reconcile: recovered orphaned trial refund booking=%s amount=%s',
                    booking.id, refunded,
                )
        except Exception as e:
            errors += 1
            logger.exception('reconcile trial refund failed booking=%s: %s', booking.id, e)

    if recovered or errors:
        logger.warning(
            'reconcile_orphaned_refunds: checked=%s recovered=%s errors=%s',
            checked, recovered, errors,
        )
    return {'checked': checked, 'recovered': recovered, 'errors': errors}


@shared_task(name='billing.reconcile_wallet_balances')
def reconcile_wallet_balances():
    """Ночная сверка денежного инварианта: balance == SUM(transactions).

    Денормализованный Wallet.balance — источник скорости, а Transaction —
    источник правды. При любом баге в логике баланс может разойтись с историей.
    Эта задача находит расхождения и громко логирует их (НЕ правит автоматически —
    авто-правка денег без ручного разбора опаснее самого расхождения).
    """
    from django.db.models import Sum
    from .models import Wallet, Transaction

    mismatches = []
    checked = 0
    for wallet in Wallet.objects.all().iterator(chunk_size=200):
        checked += 1
        agg = (Transaction.objects.filter(wallet=wallet)
               .aggregate(s=Sum('amount'))['s']) or 0
        if wallet.balance != agg:
            mismatches.append({
                'wallet': str(wallet.pk),
                'user_id': wallet.user_id,
                'balance': str(wallet.balance),
                'ledger_sum': str(agg),
                'diff': str(wallet.balance - agg),
            })

    if mismatches:
        logger.error(
            'reconcile_wallet_balances: %s MISMATCH(es) found out of %s wallets: %s',
            len(mismatches), checked, mismatches,
        )
    return {'checked': checked, 'mismatches': mismatches}


@shared_task(name='billing.expire_unpaid_approvals')
def expire_unpaid_approvals():
    """Раз в N минут: одобренные, но не оплаченные в срок заявки → EXPIRED."""
    from .services import SubscriptionService
    n = SubscriptionService.expire_unpaid_approvals()
    if n:
        logger.info(f'expire_unpaid_approvals: expired {n} unpaid approvals')
    return n


@shared_task(name='billing.settle_expired_subscriptions')
def settle_expired_subscriptions():
    """Раз в час: закрываем истёкшие ACTIVE/PAUSED подписки и сливаем escrow.

    Без этого деньги за непроведённые уроки (бесконечные переносы/неявки)
    зависали бы в escrow навсегда.
    """
    from .models import Subscription
    from .services import SubscriptionService

    threshold = timezone.now() - timedelta(hours=settings.PAYOUT_GRACE_HOURS)
    candidates = (
        Subscription.objects
        .filter(
            status__in=(Subscription.Status.ACTIVE, Subscription.Status.PAUSED),
            expires_at__lt=threshold,
        )
        .order_by('expires_at')[:200]
    )

    settled = 0
    refunded_total = 0
    errors = 0
    for sub in candidates:
        try:
            result = SubscriptionService.settle_expired(sub)
            if result is not None:
                settled += 1
                refunded_total += float(result.get('refunded') or 0)
        except Exception as e:
            errors += 1
            logger.exception(f'settle_expired failed sub={sub.id}: {e}')

    if settled or errors:
        logger.info(
            f'settle_expired_subscriptions: settled {settled}, '
            f'refunded {refunded_total}, errors {errors}'
        )
    return {'settled': settled, 'refunded': refunded_total, 'errors': errors}
