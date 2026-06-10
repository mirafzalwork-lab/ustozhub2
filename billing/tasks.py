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
    from django.db.models import Exists, OuterRef

    from teachers.models import Booking
    from .models import Transaction
    from .services import PayoutError, SubscriptionService, TrialService

    threshold = timezone.now() - timedelta(hours=settings.PAYOUT_GRACE_HOURS)

    paid = 0
    skipped = 0
    errors = 0
    total = 0

    # Выплаченные брони навсегда остаются в статусе completed/no_show_student,
    # поэтому их надо исключать ДО среза [:500] — иначе, когда выплаченных станет
    # больше 500, срез будет вечно занят ими и новые уроки не получат выплату.
    # Сверка по related_booking+type, а не по idempotency_key: Booking.id — UUID,
    # и Cast(id→text) в SQLite (hex без дефисов) не совпадает с f-string ключом.
    payout_exists = Exists(
        Transaction.objects.filter(
            related_booking=OuterRef('pk'),
            type=Transaction.Type.LESSON_PAYOUT,
        )
    )

    # === Поток 1: подписочные уроки ===
    # Доставленные = completed ИЛИ no_show_student (ученик не пришёл, урок засчитан).
    sub_candidates = (
        Booking.objects
        .filter(status__in=('completed', 'no_show_student'),
                subscription__isnull=False, slot__end_at__lt=threshold)
        .exclude(no_show_forgiven=True)   # прощённая неявка — выплаты нет (ТЗ §6)
        .exclude(dispute__status='open')  # заморозка выплаты на время спора
        .annotate(_paid=payout_exists)
        .filter(_paid=False)
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
        .annotate(_paid=payout_exists)
        .filter(_paid=False)
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
    from django.db.models import Exists, OuterRef

    from teachers.models import Booking
    from .models import Transaction
    from .services import TrialService, SubscriptionService

    REFUND_STATES = (
        'cancelled_by_student', 'cancelled_by_teacher',
        'no_show_teacher', 'expired', 'not_held',
    )
    buffer = timezone.now() - timedelta(minutes=10)

    # «Обработанные» (есть refund ИЛИ payout) брони остаются в refund-статусах
    # навсегда — исключаем их ДО среза [:500], иначе срез забьётся историей и
    # новые потерянные возвраты перестанут дозакрываться (см. release_pending_payouts).
    settled_exists = Exists(
        Transaction.objects.filter(
            related_booking=OuterRef('pk'),
            type__in=(Transaction.Type.REFUND, Transaction.Type.LESSON_PAYOUT),
        )
    )

    candidates = (
        Booking.objects
        .filter(status__in=REFUND_STATES, is_trial=True,
                trial_price_paid__isnull=False, updated_at__lt=buffer)
        .annotate(_settled=settled_exists)
        .filter(_settled=False)
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

    # --- Подписочный no_show_teacher (тот же класс бага, что и пробные) --------
    # Возврат за no_show_teacher урока подписки вызывается во view ПОСЛЕ коммита
    # статуса (booking_report_teacher_noshow_api → _refund_teacher_no_show).
    # При сбое между шагами стоимость урока зависает в escrow до settle_expired
    # (до месяца). refund_lesson идемпотентен (lesson-refund/lesson-payout) и для
    # no_show_teacher детерминирован (учитель не пришёл → всегда полный возврат),
    # поэтому повторить его из sweep безопасно.
    sub_candidates = (
        Booking.objects
        .filter(status='no_show_teacher', subscription_id__isnull=False,
                is_trial=False, updated_at__lt=buffer)
        .annotate(_settled=settled_exists)
        .filter(_settled=False)
        .order_by('updated_at')[:500]
    )
    for booking in sub_candidates:
        checked += 1
        keys = [f'lesson-refund:{booking.id}', f'lesson-payout:{booking.id}']
        if Transaction.objects.filter(idempotency_key__in=keys).exists():
            continue  # возврат или выплата уже прошли — всё ок
        try:
            refunded = SubscriptionService.refund_lesson(
                booking, cancelled_by='teacher',
                reason='Авто-сверка потерянного возврата (неявка учителя)',
            )
            if refunded:
                recovered += 1
                logger.warning(
                    'reconcile: recovered orphaned lesson refund booking=%s amount=%s',
                    booking.id, refunded,
                )
        except Exception as e:
            errors += 1
            logger.exception('reconcile lesson refund failed booking=%s: %s', booking.id, e)

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
    from decimal import Decimal

    from django.db.models import Sum
    from .models import Wallet, Transaction

    # Один GROUP BY вместо aggregate-запроса на каждый кошелёк (аудит
    # 2026-06-10 M17: при 100k кошельков ночная сверка делала 100k запросов).
    # Инвариант определён по COMPLETED (см. billing/models.py и
    # WalletService.reconcile_balance): PENDING/REVERSED в баланс не входят.
    ledger = dict(
        Transaction.objects
        .filter(status=Transaction.Status.COMPLETED)
        .values_list('wallet_id')
        .annotate(s=Sum('amount'))
        .values_list('wallet_id', 's')
    )

    mismatches = []
    checked = 0
    for wallet in Wallet.objects.all().iterator(chunk_size=200):
        checked += 1
        agg = ledger.get(wallet.pk) or Decimal('0')
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


@shared_task(name='billing.reconcile_subscription_escrow')
def reconcile_subscription_escrow():
    """Ночная сверка эскроу подписок с леджером (аудит 2026-06-10 H4).

    Деньги в эскроу не лежат ни в одном кошельке — `Subscription.escrow_balance`
    единственный их учёт, и до этой задачи он не сверялся ни с чем: баг класса
    «урок выплачен дважды» или «возврат мимо эскроу» копился бы незаметно.

    Инвариант: escrow == price_total − Σ(LESSON_PAYOUT + COMMISSION + REFUND)
    по COMPLETED-транзакциям подписки (выплата урока = payout учителю +
    комиссия платформе; возвраты — lesson-refund/sub-refund/sub-expire).

    Проверяем подписки с деньгами: ACTIVE/PAUSED + любые с escrow > 0
    (включая отменённые с удержанием под спор). Только находит и громко
    логирует — НЕ правит автоматически (как reconcile_wallet_balances).
    """
    from django.db.models import Q, Sum
    from .models import Subscription, Transaction

    subs = Subscription.objects.filter(
        Q(status__in=(Subscription.Status.ACTIVE, Subscription.Status.PAUSED))
        | Q(escrow_balance__gt=0)
    )
    moved = (
        Transaction.objects
        .filter(
            related_subscription__in=subs,
            status=Transaction.Status.COMPLETED,
            type__in=(
                Transaction.Type.LESSON_PAYOUT,
                Transaction.Type.COMMISSION,
                Transaction.Type.REFUND,
            ),
        )
        .values('related_subscription')
        .annotate(s=Sum('amount'))
    )
    moved_map = {m['related_subscription']: m['s'] for m in moved}

    mismatches = []
    checked = 0
    for sub in subs.iterator(chunk_size=200):
        checked += 1
        expected = sub.price_total - (moved_map.get(sub.pk) or 0)
        if sub.escrow_balance != expected:
            mismatches.append({
                'subscription': str(sub.pk),
                'status': sub.status,
                'escrow': str(sub.escrow_balance),
                'expected': str(expected),
                'diff': str(sub.escrow_balance - expected),
            })

    if mismatches:
        logger.error(
            'reconcile_subscription_escrow: %s MISMATCH(es) out of %s subscriptions: %s',
            len(mismatches), checked, mismatches,
        )
    return {'checked': checked, 'mismatches': mismatches}


@shared_task(name='billing.reconcile_multicard_invoices')
def reconcile_multicard_invoices():
    """Страховочная сверка зависших Multicard-инвойсов со шлюзом.

    Callback может потеряться или прийти в неверном порядке (error первой
    попытки раньше success второй) — тогда клиент заплатил, а инвойс завис в
    PROGRESS/ERROR/HOLD и кошелёк не пополнен. Опрашиваем шлюз (get_payment —
    независимое подтверждение) и дозачисляем идемпотентно (multicard:<id>).

    Окно: инвойсы не моложе 15 минут (даём callback'у прийти штатно) и не
    старше 7 дней (реально сбойные инвойсы выходят из окна и не опрашиваются
    вечно). REVERT не трогаем — терминален.
    """
    from .models import MulticardInvoice
    from .multicard import MulticardClient, MulticardError, sum_to_tiyin
    from .views import _credit_invoice, _gateway_amount_tiyin

    now = timezone.now()
    candidates = (
        MulticardInvoice.objects
        .filter(
            status__in=(
                MulticardInvoice.Status.PROGRESS,
                MulticardInvoice.Status.ERROR,
                MulticardInvoice.Status.HOLD,
            ),
            updated_at__lt=now - timedelta(minutes=15),
            created_at__gte=now - timedelta(days=7),
        )
        .exclude(multicard_uuid='')
        .order_by('updated_at')[:100]
    )

    checked = 0
    credited = 0
    errors = 0
    for inv in candidates:
        checked += 1
        try:
            data = MulticardClient().get_payment(inv.multicard_uuid)
        except MulticardError as e:
            errors += 1
            logger.info('reconcile multicard: get_payment не удался invoice=%s: %s',
                        inv.id, e)
            continue
        if data.get('status') != MulticardInvoice.Status.SUCCESS:
            continue
        gw_amount = _gateway_amount_tiyin(data)
        if gw_amount is None or gw_amount != sum_to_tiyin(inv.amount):
            logger.error(
                'reconcile multicard: сумма шлюза %s != ожидаемой %s invoice=%s',
                gw_amount, sum_to_tiyin(inv.amount), inv.id,
            )
            continue
        try:
            _credit_invoice(inv, data, gateway_confirmed=True)
            credited += 1
            logger.warning(
                'reconcile multicard: дозачислен зависший инвойс %s amount=%s '
                '(был status=%s)', inv.id, inv.amount, inv.status,
            )
        except Exception as e:
            errors += 1
            logger.exception('reconcile multicard: credit failed invoice=%s: %s',
                             inv.id, e)

    if credited or errors:
        logger.warning(
            'reconcile_multicard_invoices: checked=%s credited=%s errors=%s',
            checked, credited, errors,
        )
    return {'checked': checked, 'credited': credited, 'errors': errors}


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
    from django.db.models import Exists, OuterRef

    from teachers.models import Booking
    from .models import Subscription
    from .services import SubscriptionService

    now = timezone.now()
    threshold = now - timedelta(hours=settings.PAYOUT_GRACE_HOURS)
    # Подписки с будущими брониями ещё «доставляются» (H10: брони законно
    # выходят за expires_at) — settle_expired их пропускает. Исключаем их
    # ДО среза [:200], иначе долгоживущие подписки навсегда займут срез
    # и реально зависшие перестанут закрываться (класс бага CRIT-1).
    has_future = Exists(
        Booking.objects.filter(
            subscription=OuterRef('pk'),
            status__in=('confirmed', 'pending'),
            slot__end_at__gt=now,
        )
    )
    candidates = (
        Subscription.objects
        .filter(
            status__in=(Subscription.Status.ACTIVE, Subscription.Status.PAUSED),
            expires_at__lt=threshold,
        )
        .annotate(_future=has_future)
        .filter(_future=False)
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
