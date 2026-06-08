"""Админ-views для финансового управления через /admin-dashboard/billing/.

Доступ: только request.user.is_staff. Это НЕ Django admin — это часть кастомного
admin-dashboard'а на сайте, с удобным UX для частых операций.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.db.models import Q, Sum, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import LessonDispute, Subscription, Transaction, Wallet, WithdrawalRequest
from .platform_account import get_or_create_platform_user
from .services import (
    CancellationError, DisputeError, DisputeService,
    SubscriptionService, WalletService, WithdrawalError, WithdrawalService,
)

User = get_user_model()


def staff_required(view):
    """Декоратор: только staff. Не staff → home + flash."""
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_staff:
            messages.error(request, 'Доступ только для администраторов.')
            return redirect('home')
        return view(request, *args, **kwargs)
    return _wrapped


# ---------- Disputes (ТЗ шаг 8) ----------


@staff_required
def disputes_manage(request):
    """Список споров по урокам + разрешение (возврат / отклонение)."""
    status = request.GET.get('status', 'open')
    qs = (
        LessonDispute.objects
        .select_related('booking__slot__teacher__user', 'booking__subject', 'student')
        .order_by('-created_at')
    )
    if status in dict(LessonDispute.Status.choices):
        qs = qs.filter(status=status)
    open_count = LessonDispute.objects.filter(status=LessonDispute.Status.OPEN).count()
    return render(request, 'billing/admin/disputes.html', {
        'disputes': qs[:100], 'status': status, 'open_count': open_count,
        'statuses': LessonDispute.Status.choices,
    })


@staff_required
@require_POST
def dispute_action(request, dispute_id):
    """Админ разрешает спор: refund (ученику) или reject (выплата учителю)."""
    d = get_object_or_404(LessonDispute, pk=dispute_id)
    action = request.POST.get('action')
    note = (request.POST.get('note') or '').strip()
    try:
        if action == 'refund':
            DisputeService.resolve_refund(d, admin=request.user, note=note)
            messages.success(request, 'Спор решён в пользу ученика — средства возвращены.')
        elif action == 'reject':
            DisputeService.resolve_reject(d, admin=request.user, note=note)
            messages.success(request, 'Спор отклонён — выплата ушла учителю.')
        else:
            messages.error(request, 'Неизвестное действие.')
    except DisputeError as e:
        messages.error(request, str(e))
    return redirect('admin_billing_disputes')


# ---------- Hub ----------


@staff_required
def billing_hub(request):
    """Центральная страница финансового управления.

    Показывает ключевые метрики и ссылки на разделы.
    """
    # ~18 агрегатов на загрузку → кэшируем сводку на 45с (данные общие).
    from django.core.cache import cache as _cache
    _c = _cache.get('billing_hub_ctx')
    if _c is not None:
        return render(request, 'billing/admin/hub.html', _c)

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_ago = now - timedelta(days=30)

    platform = get_or_create_platform_user()

    # Подписки
    active_subs = Subscription.objects.filter(status='active').count()
    completed_subs = Subscription.objects.filter(status='completed').count()
    cancelled_subs = Subscription.objects.filter(
        status__in=['cancelled_by_student', 'cancelled_by_teacher', 'cancelled_by_admin'],
    ).count()

    # Деньги в системе
    total_escrow = Subscription.objects.filter(status__in=Subscription.ACTIVE_STATUSES).aggregate(
        s=Sum('escrow_balance'))['s'] or Decimal('0')
    total_user_balances = Wallet.objects.exclude(user=platform).aggregate(
        s=Sum('balance'))['s'] or Decimal('0')
    platform_balance = platform.wallet.balance

    # Транзакции за период
    tx_today = Transaction.objects.filter(created_at__gte=today_start).count()
    tx_month_revenue = Transaction.objects.filter(
        wallet=platform.wallet,
        type=Transaction.Type.COMMISSION,
        created_at__gte=month_ago,
    ).aggregate(s=Sum('amount'))['s'] or Decimal('0')

    # Выплаты учителям за месяц
    payouts_month = Transaction.objects.filter(
        type=Transaction.Type.LESSON_PAYOUT,
        created_at__gte=month_ago,
    ).aggregate(s=Sum('amount'))['s'] or Decimal('0')

    # Заявки на вывод
    pending_withdrawals = WithdrawalRequest.objects.filter(status='pending').count()
    approved_withdrawals = WithdrawalRequest.objects.filter(status='approved').count()
    withdrawals_month_amount = WithdrawalRequest.objects.filter(
        status='completed', completed_at__gte=month_ago,
    ).aggregate(s=Sum('amount'))['s'] or Decimal('0')

    # Phase 10: лента последней активности
    recent_transactions = (
        Transaction.objects
        .select_related('wallet__user')
        .order_by('-created_at')[:10]
    )
    recent_subs = (
        Subscription.objects
        .select_related('student', 'teacher__user', 'subject')
        .order_by('-created_at')[:5]
    )
    recent_withdrawals = (
        WithdrawalRequest.objects
        .select_related('user')
        .order_by('-created_at')[:5]
    )

    # Phase 10: алёрты — что требует внимания админа
    User = get_user_model()
    new_users_24h = User.objects.filter(date_joined__gte=now - timedelta(hours=24)).count()

    # Очередь модерации учителей (учителя без рассмотрения)
    from teachers.models import TeacherProfile
    pending_moderation_count = TeacherProfile.objects.filter(
        moderation_status='pending',
    ).count()
    # Orphan-учителя — есть User(type=teacher) но нет TeacherProfile (брошенная регистрация)
    orphan_teacher_users_count = User.objects.filter(user_type='teacher').exclude(
        id__in=TeacherProfile.objects.values_list('user_id', flat=True),
    ).count()

    ctx = {
        'platform': platform,
        'platform_balance': platform_balance,
        'total_escrow': total_escrow,
        'total_user_balances': total_user_balances,
        'active_subs': active_subs,
        'completed_subs': completed_subs,
        'cancelled_subs': cancelled_subs,
        'tx_today': tx_today,
        'tx_month_revenue': tx_month_revenue,
        'payouts_month': payouts_month,
        'pending_withdrawals': pending_withdrawals,
        'approved_withdrawals': approved_withdrawals,
        'withdrawals_month_amount': withdrawals_month_amount,
        # Phase 10:
        'recent_transactions': list(recent_transactions),
        'recent_subs': list(recent_subs),
        'recent_withdrawals': list(recent_withdrawals),
        'new_users_24h': new_users_24h,
        'pending_moderation_count': pending_moderation_count,
        'orphan_teacher_users_count': orphan_teacher_users_count,
    }
    _cache.set('billing_hub_ctx', ctx, 45)
    return render(request, 'billing/admin/hub.html', ctx)


# ---------- Reports: история и статистика денег ----------


def _reports_period(request):
    """Разбирает период из ?period=... или ?from=&to= в (start, end, period_key).

    start — datetime включительно (или None = «с начала»); end — datetime
    исключительно (или None = «до сейчас»). period_key — для подсветки кнопок.
    """
    from datetime import datetime

    now = timezone.now()
    custom_from = (request.GET.get('from') or '').strip()
    custom_to = (request.GET.get('to') or '').strip()

    if custom_from or custom_to:
        start = end = None
        try:
            if custom_from:
                start = timezone.make_aware(datetime.strptime(custom_from, '%Y-%m-%d'))
        except (ValueError, TypeError):
            start = None
        try:
            if custom_to:
                end = timezone.make_aware(datetime.strptime(custom_to, '%Y-%m-%d')) + timedelta(days=1)
        except (ValueError, TypeError):
            end = None
        return start, end, 'custom'

    period = request.GET.get('period', '30d')
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == 'today':
        return today_start, None, 'today'
    if period == '7d':
        return now - timedelta(days=7), None, '7d'
    if period == '90d':
        return now - timedelta(days=90), None, '90d'
    if period == 'all':
        return None, None, 'all'
    return now - timedelta(days=30), None, '30d'


@staff_required
def billing_reports(request):
    """История и статистика денежных потоков платформы.

    Отвечает на вопросы: кто сколько пополнил, сколько заработал каждый учитель,
    сколько комиссии получила платформа (и от каких уроков), сколько выведено.
    Поддерживает фильтр по периоду, фильтр истории по типу и экспорт в CSV.
    """
    from django.core.paginator import Paginator
    from django.db.models.functions import TruncDate

    now = timezone.now()
    start, end, period_key = _reports_period(request)
    platform = get_or_create_platform_user()

    def in_period(qs, field='created_at'):
        if start is not None:
            qs = qs.filter(**{f'{field}__gte': start})
        if end is not None:
            qs = qs.filter(**{f'{field}__lt': end})
        return qs

    tx = in_period(Transaction.objects.filter(status=Transaction.Status.COMPLETED))

    def total_by_type(t, qs=None):
        return (qs or tx).filter(type=t).aggregate(s=Sum('amount'))['s'] or Decimal('0')

    # ---- KPI за период ----
    deposits_total = total_by_type(Transaction.Type.DEPOSIT)
    commission_total = (
        tx.filter(wallet=platform.wallet, type=Transaction.Type.COMMISSION)
        .aggregate(s=Sum('amount'))['s'] or Decimal('0')
    )
    payouts_total = total_by_type(Transaction.Type.LESSON_PAYOUT)
    refunds_total = total_by_type(Transaction.Type.REFUND)

    withdrawals_qs = in_period(
        WithdrawalRequest.objects.filter(status='completed'), field='completed_at',
    )
    withdrawals_total = withdrawals_qs.aggregate(s=Sum('amount'))['s'] or Decimal('0')

    tx_count = tx.count()

    # ---- Кто сколько пополнил ----
    top_depositors = list(
        tx.filter(type=Transaction.Type.DEPOSIT)
        .exclude(wallet=platform.wallet)
        .values('wallet__user__id', 'wallet__user__username',
                'wallet__user__first_name', 'wallet__user__last_name')
        .annotate(total=Sum('amount'), cnt=Count('id'))
        .order_by('-total')[:15]
    )

    # ---- Заработок учителей (payout net + комиссия платформы с него + выведено) ----
    teacher_rows: dict[int, dict] = {}

    def _row(uid):
        return teacher_rows.setdefault(uid, {
            'earned': Decimal('0'), 'commission': Decimal('0'),
            'withdrawn': Decimal('0'), 'lessons': 0,
        })

    for r in (tx.filter(type=Transaction.Type.LESSON_PAYOUT)
              .values('wallet__user__id')
              .annotate(earned=Sum('amount'), cnt=Count('id'))):
        row = _row(r['wallet__user__id'])
        row['earned'] = r['earned'] or Decimal('0')
        row['lessons'] = r['cnt']

    for r in (tx.filter(type=Transaction.Type.COMMISSION,
                        related_subscription__isnull=False)
              .values('related_subscription__teacher__user__id')
              .annotate(comm=Sum('amount'))):
        uid = r['related_subscription__teacher__user__id']
        if uid is not None:
            _row(uid)['commission'] = r['comm'] or Decimal('0')

    for r in withdrawals_qs.values('user__id').annotate(s=Sum('amount')):
        _row(r['user__id'])['withdrawn'] = r['s'] or Decimal('0')

    umap = {
        u.id: u for u in
        User.objects.filter(id__in=list(teacher_rows.keys())).select_related('wallet')
    }
    teachers_list = []
    for uid, data in teacher_rows.items():
        u = umap.get(uid)
        if u is None:
            continue
        teachers_list.append({
            'user': u,
            'name': u.get_full_name() or u.username,
            'earned': data['earned'],
            'commission': data['commission'],
            'withdrawn': data['withdrawn'],
            'lessons': data['lessons'],
            'balance': getattr(getattr(u, 'wallet', None), 'balance', Decimal('0')),
        })
    teachers_list.sort(key=lambda x: x['earned'], reverse=True)
    teachers_list = teachers_list[:25]

    # ---- Доход от комиссии: по каким урокам/от кого ----
    commission_log = list(
        tx.filter(wallet=platform.wallet, type=Transaction.Type.COMMISSION)
        .select_related(
            'related_subscription__student',
            'related_subscription__teacher__user',
            'related_subscription__subject',
        )
        .order_by('-created_at')[:25]
    )

    # ---- График дохода (комиссия) по дням за последние 14 дней ----
    chart_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=13)
    daily = (
        Transaction.objects.filter(
            status=Transaction.Status.COMPLETED,
            wallet=platform.wallet,
            type=Transaction.Type.COMMISSION,
            created_at__gte=chart_start,
        )
        .annotate(d=TruncDate('created_at')).values('d')
        .annotate(s=Sum('amount')).order_by('d')
    )
    daily_map = {r['d']: (r['s'] or Decimal('0')) for r in daily}
    chart = []
    for i in range(14):
        day = (chart_start + timedelta(days=i)).date()
        chart.append({'day': day, 'value': daily_map.get(day, Decimal('0'))})
    max_val = max((c['value'] for c in chart), default=Decimal('0')) or Decimal('1')
    for c in chart:
        c['pct'] = int(round(100 * c['value'] / max_val))

    # ---- Полная история транзакций (фильтр по типу + пагинация / CSV) ----
    tx_type_filter = request.GET.get('tx_type', '')
    all_tx = in_period(
        Transaction.objects.select_related('wallet__user').order_by('-created_at')
    )
    if tx_type_filter and tx_type_filter in dict(Transaction.Type.choices):
        all_tx = all_tx.filter(type=tx_type_filter)

    if request.GET.get('export') == 'csv':
        import csv

        from django.http import HttpResponse
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="transactions.csv"'
        response.write('﻿')  # BOM — чтобы Excel корректно открыл кириллицу
        writer = csv.writer(response)
        writer.writerow(['Дата', 'Пользователь', 'Тип', 'Сумма', 'Баланс после', 'Описание'])
        for t in all_tx[:10000]:
            writer.writerow([
                timezone.localtime(t.created_at).strftime('%Y-%m-%d %H:%M'),
                t.wallet.user.username, t.get_type_display(),
                t.amount, t.balance_after, t.description,
            ])
        return response

    paginator = Paginator(all_tx, 50)
    page_obj = paginator.get_page(request.GET.get('page'))

    # querystring без page — для ссылок пагинации/экспорта
    qd = request.GET.copy()
    qd.pop('page', None)
    qd.pop('export', None)
    querystring = qd.urlencode()

    return render(request, 'billing/admin/reports.html', {
        'period_key': period_key,
        'date_from': request.GET.get('from', ''),
        'date_to': request.GET.get('to', ''),
        'deposits_total': deposits_total,
        'commission_total': commission_total,
        'payouts_total': payouts_total,
        'refunds_total': refunds_total,
        'withdrawals_total': withdrawals_total,
        'tx_count': tx_count,
        'top_depositors': top_depositors,
        'teachers_list': teachers_list,
        'commission_log': commission_log,
        'chart': chart,
        'page_obj': page_obj,
        'tx_types': Transaction.Type.choices,
        'tx_type_filter': tx_type_filter,
        'querystring': querystring,
    })


@staff_required
def billing_income(request):
    """Детализация дохода платформы — от каких уроков и сколько.

    Полная лента COMMISSION-транзакций на кошельке платформы: дата урока,
    ученик → учитель, предмет, цена урока, ставка и сумма комиссии. С фильтром
    по периоду, итогами и экспортом в CSV.
    """
    from django.core.paginator import Paginator

    start, end, period_key = _reports_period(request)
    platform = get_or_create_platform_user()

    def in_period(qs):
        if start is not None:
            qs = qs.filter(created_at__gte=start)
        if end is not None:
            qs = qs.filter(created_at__lt=end)
        return qs

    base = (
        Transaction.objects
        .filter(
            wallet=platform.wallet,
            type=Transaction.Type.COMMISSION,
            status=Transaction.Status.COMPLETED,
        )
        .select_related(
            'related_booking__slot__teacher__user',
            'related_booking__student',
            'related_booking__subject',
            'related_subscription__student',
            'related_subscription__teacher__user',
            'related_subscription__subject',
        )
        .order_by('-created_at')
    )
    qs = in_period(base)

    # ---- Итоги ----
    agg = qs.aggregate(total=Sum('amount'), cnt=Count('id'))
    period_total = agg['total'] or Decimal('0')
    period_count = agg['cnt'] or 0
    avg_commission = (period_total / period_count) if period_count else Decimal('0')
    all_time_total = base.aggregate(s=Sum('amount'))['s'] or Decimal('0')

    # ---- CSV ----
    if request.GET.get('export') == 'csv':
        import csv

        from django.http import HttpResponse
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="platform_income.csv"'
        response.write('﻿')  # BOM для Excel
        writer = csv.writer(response)
        writer.writerow(['Дата начисления', 'Дата урока', 'Ученик', 'Учитель',
                         'Предмет', 'Цена урока', 'Комиссия', 'Источник'])
        for t in qs[:10000]:
            row = _income_row(t)
            writer.writerow([
                timezone.localtime(t.created_at).strftime('%Y-%m-%d %H:%M'),
                (timezone.localtime(row['lesson_date']).strftime('%Y-%m-%d %H:%M')
                 if row['lesson_date'] else '—'),
                row['student_name'], row['teacher_name'],
                row['subject_name'] or '—',
                row['price'] if row['price'] is not None else '',
                t.amount, row['source'],
            ])
        return response

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get('page'))
    rows = [dict(_income_row(t), tx=t) for t in page_obj.object_list]

    qd = request.GET.copy()
    qd.pop('page', None)
    qd.pop('export', None)
    querystring = qd.urlencode()

    return render(request, 'billing/admin/income.html', {
        'period_key': period_key,
        'date_from': request.GET.get('from', ''),
        'date_to': request.GET.get('to', ''),
        'period_total': period_total,
        'period_count': period_count,
        'avg_commission': avg_commission,
        'all_time_total': all_time_total,
        'rows': rows,
        'page_obj': page_obj,
        'querystring': querystring,
    })


def _income_row(t) -> dict:
    """Собирает данные строки дохода из COMMISSION-транзакции.

    Данные берутся из подписки (если урок по подписке) либо из брони (пробный
    урок — related_subscription может быть пустым).
    """
    b = t.related_booking
    s = t.related_subscription
    student = s.student if s else (b.student if b else None)
    if s:
        teacher_user = s.teacher.user
    elif b and b.slot_id:
        teacher_user = b.slot.teacher.user
    else:
        teacher_user = None
    subject = s.subject if s else (b.subject if b else None)
    lesson_date = b.slot.start_at if (b and b.slot_id) else None
    price = s.price_per_lesson if s else None
    source = 'Подписка' if s else ('Пробный урок' if b else 'Прочее')
    return {
        'student_name': (student.get_full_name() or student.username) if student else '—',
        'student_username': student.username if student else '',
        'teacher_name': (teacher_user.get_full_name() or teacher_user.username) if teacher_user else '—',
        'teacher_username': teacher_user.username if teacher_user else '',
        'subject_name': subject.name if subject else None,
        'lesson_date': lesson_date,
        'price': price,
        'source': source,
    }


# ---------- Wallet search + top-up ----------


@staff_required
def wallet_search(request):
    """Поиск пользователя по username/email/id → детали кошелька + top-up форма."""
    q = (request.GET.get('q') or '').strip()
    user = None
    results = []
    transactions = None

    if q:
        # Поиск
        results_qs = (
            User.objects
            .filter(Q(username__icontains=q) | Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
            .exclude(username='__platform__')
            .order_by('username')[:20]
        )
        results = list(results_qs)
        # Если один результат — открываем его
        if len(results) == 1:
            user = results[0]

    # Прямое открытие по id
    user_id = request.GET.get('user_id')
    if user_id:
        user = User.objects.filter(pk=user_id).first()

    if user is not None:
        wallet, _ = Wallet.objects.get_or_create(user=user)
        transactions = (
            Transaction.objects
            .filter(wallet=wallet)
            .order_by('-created_at')[:30]
        )
    else:
        wallet = None

    return render(request, 'billing/admin/wallets.html', {
        'q': q, 'results': results, 'user': user,
        'wallet': wallet, 'transactions': transactions,
        # Одноразовый токен идемпотентности для формы top-up (см. wallet_topup_action).
        'op_token': uuid.uuid4().hex,
    })


@staff_required
@require_POST
def wallet_topup_action(request, user_id):
    """Пополнить / списать / скорректировать кошелёк пользователя."""
    target = get_object_or_404(User, pk=user_id)
    operation = request.POST.get('operation')  # 'credit' / 'debit'
    raw_amount = (request.POST.get('amount') or '').strip().replace(' ', '').replace(',', '.')
    reason = (request.POST.get('reason') or '').strip()[:300]

    back_url = f"{reverse('admin_billing_wallets')}?user_id={target.pk}"

    if operation not in ('credit', 'debit'):
        messages.error(request, 'Неверная операция.')
        return redirect(back_url)

    try:
        amount = Decimal(raw_amount)
    except (InvalidOperation, ValueError):
        messages.error(request, 'Некорректная сумма.')
        return redirect(back_url)

    if amount <= 0:
        messages.error(request, 'Сумма должна быть положительной.')
        return redirect(back_url)

    # Идемпотентность: ключ выводится из одноразового токена формы, а НЕ из
    # свежего uuid на каждый запрос. Двойной клик / refresh / resubmit шлёт тот
    # же op_token → WalletService увидит существующую транзакцию и сделает no-op
    # вместо повторного зачисления. Fallback на uuid — только если токена нет
    # (старая открытая вкладка), чтобы не сломать операцию.
    op_token = (request.POST.get('op_token') or '').strip()[:64]
    if op_token:
        idem = f'admin-{operation}:{target.pk}:{op_token}'
    else:
        idem = f'admin-{operation}:{uuid.uuid4()}'
    description = f'[admin {request.user.username}] {reason or operation}'

    try:
        if operation == 'credit':
            WalletService.credit(
                user=target, amount=amount,
                tx_type=Transaction.Type.ADJUSTMENT_IN if reason else Transaction.Type.DEPOSIT,
                idempotency_key=idem, description=description,
            )
            messages.success(
                request,
                f'✅ Кошелёк {target.username} пополнен на {amount:,.0f} сум.'.replace(',', ' '),
            )
            # Замыкаем loop: студент ждал зачисления — даём знать
            _notify_topup(target, amount, credited=True)
        else:  # debit
            WalletService.debit(
                user=target, amount=amount,
                tx_type=Transaction.Type.ADJUSTMENT_OUT,
                idempotency_key=idem, description=description,
            )
            messages.success(
                request,
                f'✅ С кошелька {target.username} списано {amount:,.0f} сум.'.replace(',', ' '),
            )
            _notify_topup(target, amount, credited=False)
    except Exception as e:
        messages.error(request, f'Ошибка: {e}')

    return redirect(back_url)


def _notify_topup(user, amount, credited: bool) -> None:
    """In-app уведомление пользователю об изменении баланса.

    Не критично — заворачиваем в try/except, чтобы не сорвать основную операцию.
    """
    try:
        from teachers.models import Notification

        amount_str = f'{int(amount):,}'.replace(',', ' ')
        balance_str = f'{int(user.wallet.balance):,}'.replace(',', ' ')

        if credited:
            title = 'Кошелёк пополнен'
            short = f'+{amount_str} сум. Баланс: {balance_str} сум.'
            full = (
                f'Ваш кошелёк пополнен на {amount_str} сум. '
                f'Текущий баланс: {balance_str} сум. '
                f'Теперь вы можете оформить подписку или забронировать уроки.'
            )
            category = Notification.Category.PAYMENT_IN
        else:
            title = 'Списание с кошелька'
            short = f'−{amount_str} сум. Баланс: {balance_str} сум.'
            full = (
                f'С вашего кошелька списано {amount_str} сум. '
                f'Текущий баланс: {balance_str} сум.'
            )
            category = Notification.Category.PAYMENT_OUT

        Notification.objects.create(
            target='specific_user',
            target_user=user,
            title=title,
            short_text=short,
            full_text=full,
            priority=10,
            category=category,
            action_url='/billing/my/wallet/topup/' if credited else '/billing/my/subscriptions/',
        )
    except Exception:
        # Уведомление не критично — не блокируем wallet-операцию
        pass


# ---------- Withdrawals manage ----------


@staff_required
def withdrawals_manage(request):
    """Список заявок на вывод с фильтром по статусу + inline-actions."""
    status_filter = request.GET.get('status') or 'pending'
    valid_statuses = [c[0] for c in WithdrawalRequest.Status.choices] + ['all']
    if status_filter not in valid_statuses:
        status_filter = 'pending'

    qs = (
        WithdrawalRequest.objects
        .select_related('user', 'reviewed_by')
        .order_by('-created_at')
    )
    if status_filter != 'all':
        qs = qs.filter(status=status_filter)

    items = list(qs[:100])
    counts = dict(
        WithdrawalRequest.objects.values_list('status').annotate(c=Count('id'))
    )

    return render(request, 'billing/admin/withdrawals.html', {
        'items': items,
        'status_filter': status_filter,
        'counts': counts,
    })


@staff_required
@require_POST
def withdrawal_action(request, wr_id):
    """approve / reject / complete заявки на вывод."""
    wr = get_object_or_404(WithdrawalRequest, pk=wr_id)
    action = request.POST.get('action')
    note = (request.POST.get('note') or '').strip()

    try:
        if action == 'approve':
            WithdrawalService.approve(wr, admin_user=request.user, note=note)
            messages.success(request, f'Заявка #{str(wr.id)[:8]} одобрена.')
        elif action == 'complete':
            WithdrawalService.complete(wr, admin_user=request.user, note=note)
            messages.success(request, f'Заявка #{str(wr.id)[:8]} завершена.')
        elif action == 'reject':
            if not note:
                messages.error(request, 'Для отклонения требуется причина.')
            else:
                WithdrawalService.reject(wr, admin_user=request.user, note=note)
                messages.success(request, f'Заявка #{str(wr.id)[:8]} отклонена, средства возвращены.')
        else:
            messages.error(request, 'Неизвестное действие.')
    except WithdrawalError as e:
        messages.error(request, str(e))

    return redirect('admin_billing_withdrawals')


# ---------- Subscriptions manage ----------


@staff_required
def subscriptions_manage(request):
    """Список подписок с фильтром по статусу."""
    status_filter = request.GET.get('status') or 'active'
    valid = [c[0] for c in Subscription.Status.choices] + ['all']
    if status_filter not in valid:
        status_filter = 'active'

    qs = (
        Subscription.objects
        .select_related('student', 'teacher__user', 'subject')
        .order_by('-created_at')
    )
    if status_filter != 'all':
        qs = qs.filter(status=status_filter)

    items = list(qs[:100])
    counts = dict(
        Subscription.objects.values_list('status').annotate(c=Count('id'))
    )

    return render(request, 'billing/admin/subscriptions.html', {
        'items': items,
        'status_filter': status_filter,
        'counts': counts,
    })


@staff_required
@require_POST
def subscription_admin_cancel(request, sub_id):
    """Админ принудительно отменяет подписку (с refund)."""
    sub = get_object_or_404(Subscription, pk=sub_id)
    reason = (request.POST.get('reason') or '').strip()
    try:
        result = SubscriptionService.cancel(sub, cancelled_by='admin', reason=reason or 'admin action')
        messages.success(
            request,
            f'Подписка отменена. Возврат: {int(result["refunded"])} сум, '
            f'выплачено учителю за уже проведённые: {result["paid_out"]}.'
        )
    except CancellationError as e:
        messages.error(request, str(e))
    return redirect('admin_billing_subscriptions')
