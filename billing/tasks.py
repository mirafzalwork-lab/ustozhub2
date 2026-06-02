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
    sub_candidates = (
        Booking.objects
        .filter(status='completed', subscription__isnull=False, slot__end_at__lt=threshold)
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
            status='completed', is_trial=True,
            trial_price_paid__isnull=False,
            slot__end_at__lt=threshold,
        )
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


@shared_task(name='billing.expire_unpaid_approvals')
def expire_unpaid_approvals():
    """Раз в N минут: одобренные, но не оплаченные в срок заявки → EXPIRED."""
    from .services import SubscriptionService
    n = SubscriptionService.expire_unpaid_approvals()
    if n:
        logger.info(f'expire_unpaid_approvals: expired {n} unpaid approvals')
    return n
