from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from teachers.models import TeacherProfile

from .forms import HomeworkForm, HomeworkGradeForm, HomeworkSubmissionForm, TariffForm, WithdrawalRequestForm
from .models import (
    Homework, HomeworkAttachment, HomeworkSubmission, HomeworkSubmissionFile,
    Subscription, Tariff, Transaction, WithdrawalRequest,
)
from .validators import validate_homework_file
from .services import (
    AlreadySubscribed,
    CancellationError,
    InsufficientFunds,
    SubscriptionService,
    WithdrawalError,
    WithdrawalService,
)


def _get_teacher_or_403(request):
    """Возвращает TeacherProfile текущего пользователя или редиректит на home."""
    try:
        return request.user.teacher_profile
    except TeacherProfile.DoesNotExist:
        messages.error(request, 'Эта страница доступна только учителям.')
        return None


@login_required
def tariffs_list(request):
    teacher = _get_teacher_or_403(request)
    if teacher is None:
        return redirect('home')

    tariffs = Tariff.objects.filter(teacher=teacher).select_related('subject')
    return render(request, 'billing/tariffs_list.html', {
        'tariffs': tariffs,
        'teacher': teacher,
    })


@login_required
def tariff_create(request):
    teacher = _get_teacher_or_403(request)
    if teacher is None:
        return redirect('home')

    if request.method == 'POST':
        form = TariffForm(request.POST, teacher=teacher)
        if form.is_valid():
            tariff = form.save()
            messages.success(request, f'Тариф «{tariff.name or tariff.subject}» создан.')
            return redirect('tariffs_list')
    else:
        form = TariffForm(teacher=teacher)

    return render(request, 'billing/tariff_form.html', {
        'form': form,
        'teacher': teacher,
        'is_create': True,
    })


@login_required
def tariff_edit(request, pk):
    teacher = _get_teacher_or_403(request)
    if teacher is None:
        return redirect('home')

    tariff = get_object_or_404(Tariff, pk=pk, teacher=teacher)

    if request.method == 'POST':
        form = TariffForm(request.POST, instance=tariff, teacher=teacher)
        if form.is_valid():
            form.save()
            messages.success(request, 'Тариф обновлён.')
            return redirect('tariffs_list')
    else:
        form = TariffForm(instance=tariff, teacher=teacher)

    return render(request, 'billing/tariff_form.html', {
        'form': form,
        'teacher': teacher,
        'tariff': tariff,
        'is_create': False,
    })


@login_required
@require_POST
def tariff_delete(request, pk):
    teacher = _get_teacher_or_403(request)
    if teacher is None:
        return redirect('home')

    tariff = get_object_or_404(Tariff, pk=pk, teacher=teacher)
    tariff.delete()
    messages.success(request, 'Тариф удалён.')
    return redirect('tariffs_list')


@login_required
@require_POST
def tariff_toggle_active(request, pk):
    teacher = _get_teacher_or_403(request)
    if teacher is None:
        return redirect('home')

    tariff = get_object_or_404(Tariff, pk=pk, teacher=teacher)
    tariff.is_active = not tariff.is_active
    tariff.save(update_fields=['is_active', 'updated_at'])
    state = 'включён' if tariff.is_active else 'выключен'
    messages.success(request, f'Тариф {state}.')
    return redirect('tariffs_list')


# ---------- Wallet topup (manual flow до Payme/Click) --------------------


@login_required
def wallet_topup_request(request):
    """Публичная страница пополнения кошелька.

    MVP: показывает реквизиты карты + Telegram-handle для подтверждения перевода.
    Админ начисляет вручную через /admin/billing/wallets/<user_id>/topup/.
    """
    wallet = request.user.wallet

    try:
        amount = int(float(request.GET.get('amount') or 0))
    except (TypeError, ValueError):
        amount = 0
    amount = max(amount, 0)

    needed = max(amount - int(wallet.balance), 0) if amount else 0

    next_url = request.GET.get('next', '')
    if next_url and not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = ''

    return render(request, 'billing/topup_request.html', {
        'wallet': wallet,
        'amount': amount,
        'needed': needed,
        'next_url': next_url,
        'topup_configured': bool(getattr(settings, 'TOPUP_CARD_NUMBER', '')),
        'card_number': getattr(settings, 'TOPUP_CARD_NUMBER', ''),
        'card_holder': getattr(settings, 'TOPUP_CARD_HOLDER', ''),
        'bank_name': getattr(settings, 'TOPUP_BANK_NAME', ''),
        'telegram_handle': getattr(settings, 'TOPUP_TELEGRAM_HANDLE', ''),
        'support_phone': getattr(settings, 'TOPUP_SUPPORT_PHONE', ''),
        'processing_hours': getattr(settings, 'TOPUP_PROCESSING_HOURS', '1-2'),
    })


# ---------- Subscription (покупка / список / отмена) ----------------------


@login_required
def subscription_buy(request, tariff_id):
    """LEGACY: мгновенная покупка подписки без одобрения учителя выведена из эксплуатации.

    Канонический сценарий — единственный: заявка → одобрение учителем → оплата →
    выбор расписания (см. ``continue_learning`` и ТЗ-шаги 2–6 ниже). Мгновенное
    списание в обход одобрения создавало второй, непредсказуемый платёжный путь.

    Этот URL сохранён только ради старых ссылок/закладок: он перенаправляет на
    оформление обучения у того же учителя по тому же предмету. Сам движок
    ``SubscriptionService.purchase`` остаётся (используется в тестах и админке).
    """
    tariff = get_object_or_404(
        Tariff.objects.select_related('teacher', 'subject'),
        pk=tariff_id,
        is_active=True,
    )
    url = reverse('continue_learning', kwargs={'teacher_id': tariff.teacher_id})
    return redirect(f'{url}?subject={tariff.subject_id}')


# ---------- ТЗ flow: заявка → одобрение → оплата → бронь -------------------


_WEEKDAY_RU = {
    'monday': 'Понедельник', 'tuesday': 'Вторник', 'wednesday': 'Среда',
    'thursday': 'Четверг', 'friday': 'Пятница', 'saturday': 'Суббота', 'sunday': 'Воскресенье',
}
_WEEKDAY_ORDER = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']


@login_required
def continue_learning(request, teacher_id):
    """ТЗ шаги 2-3: после пробного — выбор тарифа и отправка заявки на обучение."""
    teacher = get_object_or_404(
        TeacherProfile.objects.select_related('user'),
        pk=teacher_id, is_active=True, moderation_status='approved',
    )
    if request.user.user_type != 'student':
        messages.error(request, 'Только ученик может оформить обучение.')
        return redirect('teacher_detail', id=teacher.id)
    if teacher.user_id == request.user.id:
        messages.error(request, 'Нельзя оформить обучение у самого себя.')
        return redirect('teacher_detail', id=teacher.id)

    from teachers.models import Booking, Subject, TeacherSubject

    # Предмет: из формы/URL, иначе из последнего пробного, иначе первый предмет учителя.
    subject = None
    subject_id = request.POST.get('subject_id') or request.GET.get('subject')
    if subject_id:
        subject = Subject.objects.filter(pk=subject_id).first()
    if subject is None:
        last_trial = (
            Booking.objects.filter(student=request.user, slot__teacher=teacher, is_trial=True)
            .select_related('subject').order_by('-created_at').first()
        )
        if last_trial and last_trial.subject_id:
            subject = last_trial.subject
    if subject is None:
        ts = TeacherSubject.objects.filter(teacher=teacher).select_related('subject').first()
        subject = ts.subject if ts else None
    if subject is None:
        messages.error(request, 'У учителя не указаны предметы.')
        return redirect('teacher_detail', id=teacher.id)

    real_tariffs = list(
        Tariff.objects.filter(teacher=teacher, subject=subject, is_active=True)
        .order_by('lessons_per_week')
    )
    standard = [] if real_tariffs else SubscriptionService.standard_tariff_options(teacher, subject)

    if request.method == 'POST':
        preferred = (request.POST.get('preferred_schedule') or '').strip()
        idem = request.POST.get('idempotency_key') or str(uuid.uuid4())
        try:
            if real_tariffs:
                tariff = get_object_or_404(
                    Tariff, pk=request.POST.get('tariff_id'),
                    teacher=teacher, subject=subject, is_active=True,
                )
                params = dict(
                    lessons_per_week=tariff.lessons_per_week,
                    lesson_duration_minutes=tariff.lesson_duration_minutes,
                    duration_months=tariff.duration_months,
                    price_per_month=tariff.price_per_month, tariff=tariff,
                )
            else:
                lpw = int(request.POST.get('lessons_per_week') or 0)
                opt = next((o for o in standard if o['lessons_per_week'] == lpw), None)
                if not opt:
                    raise ValueError('Выберите тариф.')
                params = dict(
                    lessons_per_week=opt['lessons_per_week'],
                    lesson_duration_minutes=opt['lesson_duration_minutes'],
                    duration_months=opt['duration_months'],
                    price_per_month=opt['price_per_month'], tariff=None,
                )
            SubscriptionService.create_request(
                student=request.user, teacher=teacher, subject=subject,
                preferred_schedule=preferred,
                idempotency_key=f'web-req:{request.user.id}:{teacher.id}:{subject.id}:{idem}',
                **params,
            )
            messages.success(
                request,
                'Заявка отправлена учителю. Мы уведомим вас, когда её подтвердят.',
            )
            return redirect('my_subscriptions')
        except AlreadySubscribed as e:
            messages.warning(request, str(e))
        except ValueError as e:
            messages.error(request, str(e))

    return render(request, 'billing/continue_learning.html', {
        'teacher': teacher, 'subject': subject,
        'real_tariffs': real_tariffs, 'standard': standard,
        'idempotency_key': str(uuid.uuid4()),
    })


@login_required
def teacher_learning_requests(request):
    """ТЗ шаг 4: учитель видит заявки на обучение и подтверждает/отклоняет."""
    teacher = _get_teacher_or_403(request)
    if teacher is None:
        return redirect('home')
    requests_qs = (
        Subscription.objects
        .filter(teacher=teacher, status=Subscription.Status.PENDING_APPROVAL)
        .select_related('student', 'subject').order_by('created_at')
    )
    return render(request, 'billing/learning_requests.html', {'requests': requests_qs})


@login_required
@require_POST
def learning_request_action(request, sub_id):
    """Учитель подтверждает (approve) или отклоняет (reject) заявку."""
    teacher = _get_teacher_or_403(request)
    if teacher is None:
        return redirect('home')
    sub = get_object_or_404(Subscription, pk=sub_id, teacher=teacher)
    action = request.POST.get('action')
    try:
        if action == 'approve':
            SubscriptionService.approve_request(sub)
            messages.success(request, 'Заявка подтверждена. Ученик получит уведомление об оплате.')
        elif action == 'reject':
            SubscriptionService.reject_request(sub, reason=(request.POST.get('reason') or '').strip())
            messages.success(request, 'Заявка отклонена.')
        else:
            messages.error(request, 'Неизвестное действие.')
    except ValueError as e:
        messages.error(request, str(e))
    return redirect('teacher_learning_requests')


@login_required
def subscription_pay(request, sub_id):
    """ТЗ шаг 5: ученик оплачивает одобренную заявку (escrow → ACTIVE)."""
    sub = get_object_or_404(
        Subscription.objects.select_related('teacher__user', 'subject'),
        pk=sub_id, student=request.user,
    )
    if sub.status != Subscription.Status.PENDING_PAYMENT:
        messages.info(request, f'Оплата недоступна: статус «{sub.get_status_display()}».')
        return redirect('my_subscriptions')

    wallet = request.user.wallet
    has_enough = wallet.balance >= sub.price_total
    needed_amount = max(int(sub.price_total - wallet.balance), 0)

    if request.method == 'POST':
        try:
            SubscriptionService.pay(sub, idempotency_key=request.POST.get('idempotency_key') or '')
            messages.success(request, 'Оплата прошла! Теперь выберите удобное расписание.')
            return redirect('subscription_schedule', sub_id=sub.id)
        except InsufficientFunds as e:
            messages.error(request, str(e))
        except ValueError as e:
            messages.error(request, str(e))

    return render(request, 'billing/subscription_pay.html', {
        'sub': sub, 'wallet': wallet, 'has_enough': has_enough,
        'needed_amount': needed_amount, 'idempotency_key': str(uuid.uuid4()),
    })


@login_required
def subscription_schedule(request, sub_id):
    """ТЗ шаг 6: ученик выбирает недельный шаблон → бронируются все уроки."""
    sub = get_object_or_404(
        Subscription.objects.select_related('teacher__user', 'subject'),
        pk=sub_id, student=request.user,
    )
    if sub.status != Subscription.Status.ACTIVE:
        messages.info(request, 'Расписание доступно только для оплаченной подписки.')
        return redirect('my_subscriptions')
    _active_bk = sub.bookings.exclude(
        status__in=['cancelled_by_student', 'cancelled_by_teacher']
    )
    booked_count = _active_bk.count()
    if booked_count >= sub.total_lessons:
        messages.info(request, 'Все уроки уже забронированы.')
        return redirect('my_bookings_page')

    # Кандидаты — ТОЛЬКО реальные свободные слоты из календаря учителя
    # (status='free', будущие, длительностью ровно как урок). Ничего не выдумываем.
    from datetime import timedelta as _td
    from django.utils import timezone as _tz
    from teachers.models import TimeSlot
    now = _tz.now()
    dur = _td(minutes=sub.lesson_duration_minutes)
    slot_counts = {}  # (day_key, 'HH:MM') -> число свободных слотов вперёд
    free_slots = (
        TimeSlot.objects
        .filter(teacher=sub.teacher, status='free', start_at__gte=now)
        .order_by('start_at')
    )
    for s in free_slots:
        if (s.end_at - s.start_at) != dur:
            continue
        local = _tz.localtime(s.start_at)
        key = (_WEEKDAY_ORDER[local.weekday()], local.strftime('%H:%M'))
        slot_counts[key] = slot_counts.get(key, 0) + 1

    candidates = []  # [{'day','day_ru','time','value','label','count'}]
    for day in _WEEKDAY_ORDER:
        for t in sorted(tm for (d, tm) in slot_counts if d == day):
            candidates.append({
                'day': day, 'day_ru': _WEEKDAY_RU[day], 'time': t,
                'value': f'{day}|{t}',
                'label': f'{_WEEKDAY_RU[day]} {t}',
                'count': slot_counts[(day, t)],
            })

    if request.method == 'POST':
        selected = request.POST.getlist('slot')
        pattern = []
        for val in selected:
            if '|' in val:
                d, t = val.split('|', 1)
                if d in _WEEKDAY_RU:
                    pattern.append({'day': d, 'time': t})
        if len(pattern) != sub.lessons_per_week:
            messages.error(
                request,
                f'Выберите ровно {sub.lessons_per_week} занятия в неделю (выбрано {len(pattern)}).',
            )
        else:
            try:
                created = SubscriptionService.book_schedule(sub, pattern)
                total_booked = sub.bookings.exclude(
                    status__in=['cancelled_by_student', 'cancelled_by_teacher']
                ).count()
                if total_booked >= sub.total_lessons:
                    messages.success(
                        request,
                        f'Расписание сформировано: забронировано {len(created)} уроков.',
                    )
                    return redirect('my_bookings_page')
                # Частично: свободных слотов учителя не хватило на весь объём.
                messages.success(
                    request,
                    f'Забронировано ещё {len(created)} уроков по свободным слотам учителя '
                    f'({total_booked} из {sub.total_lessons}).',
                )
                messages.info(
                    request,
                    'Остальные уроки можно добрать здесь же, когда учитель откроет новые '
                    'слоты в календаре — напишите ему с просьбой добавить время.',
                )
                return redirect('subscription_schedule', sub_id=sub.id)
            except ValueError as e:
                messages.error(request, str(e))

    return render(request, 'billing/subscription_schedule.html', {
        'sub': sub, 'candidates': candidates,
        'booked_count': booked_count,
        'remaining': sub.total_lessons - booked_count,
    })


# ---------- Disputes (ТЗ шаг 8): ученик открывает/отзывает ----------------


@login_required
def dispute_open(request, booking_id):
    """Ученик открывает спор по проведённому оплаченному уроку."""
    from teachers.models import Booking
    from .services import DisputeError, DisputeService
    booking = get_object_or_404(
        Booking.objects.select_related('slot__teacher__user', 'subject'),
        pk=booking_id, student=request.user,
    )
    existing = getattr(booking, 'dispute', None)
    if request.method == 'POST':
        reason = (request.POST.get('reason') or '').strip()
        if len(reason) < 10:
            messages.error(request, 'Опишите проблему подробнее (минимум 10 символов).')
        else:
            try:
                DisputeService.open(booking, student=request.user, reason=reason)
                messages.success(
                    request,
                    'Спор открыт. Администрация рассмотрит его; выплата учителю заморожена.',
                )
                return redirect('my_bookings_page')
            except DisputeError as e:
                messages.error(request, str(e))
    return render(request, 'billing/dispute_open.html', {
        'booking': booking, 'existing': existing,
    })


@login_required
@require_POST
def dispute_cancel(request, dispute_id):
    """Ученик отзывает свой открытый спор."""
    from .models import LessonDispute
    from .services import DisputeError, DisputeService
    d = get_object_or_404(LessonDispute, pk=dispute_id, student=request.user)
    try:
        DisputeService.cancel(d, student=request.user)
        messages.success(request, 'Спор отозван.')
    except DisputeError as e:
        messages.error(request, str(e))
    return redirect('my_bookings_page')


@login_required
def my_subscriptions(request):
    """Все подписки текущего ученика (активные + история).

    Также передаёт `pending_reviews` — completed bookings без Review,
    чтобы ученик мог оценить каждый урок отдельно.
    """
    from teachers.models import Booking
    from django.db.models import Count, Q

    subs = (
        Subscription.objects
        .filter(student=request.user)
        .select_related('teacher__user', 'subject', 'tariff')
        .annotate(num_active_bookings=Count('bookings', filter=~Q(
            bookings__status__in=['cancelled_by_student', 'cancelled_by_teacher'])))
        .order_by('-created_at')
    )
    active = [s for s in subs if s.status in Subscription.ACTIVE_STATUSES]
    history = [s for s in subs if s.status not in Subscription.ACTIVE_STATUSES]

    # Уроки, которые прошли но ученик ещё не оценил
    pending_reviews = (
        Booking.objects
        .filter(student=request.user, status='completed', review__isnull=True)
        .select_related('slot__teacher__user', 'subject', 'subscription')
        .order_by('-slot__end_at')[:20]
    )

    return render(request, 'billing/my_subscriptions.html', {
        'active': active,
        'history': history,
        'pending_reviews': pending_reviews,
    })


@login_required
@require_POST
def subscription_cancel(request, sub_id):
    """Отменить подписку. Доступно: ученику-владельцу, учителю-владельцу, staff.

    POST params:
      reason: optional, причина отмены (до 1000 симв.)
    """
    sub = get_object_or_404(
        Subscription.objects.select_related('student', 'teacher__user'),
        pk=sub_id,
    )

    # Определяем роль отменяющего
    if request.user.is_staff:
        cancelled_by = 'admin'
    elif sub.student_id == request.user.id:
        cancelled_by = 'student'
    elif sub.teacher.user_id == request.user.id:
        cancelled_by = 'teacher'
    else:
        messages.error(request, 'У вас нет прав отменить эту подписку.')
        return redirect('my_subscriptions')

    reason = (request.POST.get('reason') or '').strip()
    try:
        result = SubscriptionService.cancel(sub, cancelled_by=cancelled_by, reason=reason)
    except CancellationError as e:
        messages.error(request, str(e))
        if cancelled_by == 'student':
            return redirect('my_subscriptions')
        return redirect('teacher_subscribers')

    refunded = result['refunded']
    messages.success(
        request,
        f'Подписка отменена. Возвращено на баланс: {int(refunded)} сум. '
        f'Отменено уроков: {result["cancelled_bookings"]}.'
    )
    return redirect('my_subscriptions' if cancelled_by == 'student' else 'teacher_subscribers')


@login_required
@require_POST
def subscription_pause(request, sub_id):
    """Ученик приостанавливает активную подписку (v2 Шаг 6 → UI)."""
    sub = get_object_or_404(
        Subscription.objects.select_related('student', 'teacher__user'), pk=sub_id,
    )
    if sub.student_id != request.user.id:
        messages.error(request, 'У вас нет прав приостановить эту подписку.')
        return redirect('my_subscriptions')
    reason = (request.POST.get('reason') or '').strip()
    try:
        freed = SubscriptionService.pause(sub, reason=reason)
        messages.success(
            request,
            f'Подписка приостановлена. Снято будущих уроков: {freed}. '
            f'Возобновите в любой момент — срок продлится на время паузы.'
        )
    except CancellationError as e:
        messages.error(request, str(e))
    return redirect('my_subscriptions')


@login_required
@require_POST
def subscription_resume(request, sub_id):
    """Ученик возобновляет приостановленную подписку (v2 Шаг 6 → UI)."""
    sub = get_object_or_404(
        Subscription.objects.select_related('student', 'teacher__user'), pk=sub_id,
    )
    if sub.student_id != request.user.id:
        messages.error(request, 'У вас нет прав возобновить эту подписку.')
        return redirect('my_subscriptions')
    try:
        created = SubscriptionService.resume(sub)
        messages.success(
            request,
            f'Подписка возобновлена. Запланировано уроков: {created}.'
        )
    except CancellationError as e:
        messages.error(request, str(e))
    return redirect('my_subscriptions')


# ---------- Withdrawal ----------------------------------------------------


@login_required
def withdrawals_list(request):
    """Заявки учителя на вывод средств + форма создания новой."""
    teacher = _get_teacher_or_403(request)
    if teacher is None:
        return redirect('home')

    wallet = request.user.wallet
    reqs = WithdrawalRequest.objects.filter(user=request.user).order_by('-created_at')

    if request.method == 'POST':
        form = WithdrawalRequestForm(
            request.POST, user=request.user, max_amount=wallet.balance,
        )
        if form.is_valid():
            idem = request.POST.get('idempotency_key') or str(uuid.uuid4())
            try:
                wr = WithdrawalService.create_request(
                    user=request.user,
                    amount=form.cleaned_data['amount'],
                    payout_method=form.cleaned_data['payout_method'],
                    payout_details=form.cleaned_data['payout_details'],
                    comment=form.cleaned_data.get('comment', ''),
                    idempotency_key=f'web:{request.user.id}:{idem}',
                )
                messages.success(
                    request,
                    f'Заявка на вывод {int(wr.amount)} сум создана. Ожидайте подтверждения.'
                )
                return redirect('withdrawals_list')
            except InsufficientFunds as e:
                messages.error(request, str(e))
            except WithdrawalError as e:
                messages.error(request, str(e))
    else:
        form = WithdrawalRequestForm(user=request.user, max_amount=wallet.balance)

    return render(request, 'billing/withdrawals_list.html', {
        'form': form,
        'wallet': wallet,
        'requests': reqs,
        'idempotency_key': str(uuid.uuid4()),
    })


@login_required
@require_POST
def withdrawal_cancel(request, wr_id):
    wr = get_object_or_404(WithdrawalRequest, pk=wr_id, user=request.user)
    try:
        WithdrawalService.cancel_by_user(wr)
        messages.success(request, f'Заявка отменена, {int(wr.amount)} сум возвращены на баланс.')
    except WithdrawalError as e:
        messages.error(request, str(e))
    return redirect('withdrawals_list')


@login_required
def teacher_subscribers(request):
    """Активные подписчики учителя — кто сейчас учится у меня по подписке."""
    from datetime import timedelta
    from django.db.models import Sum
    from django.utils import timezone

    teacher = _get_teacher_or_403(request)
    if teacher is None:
        return redirect('home')

    subs = (
        Subscription.objects
        .filter(teacher=teacher)
        .select_related('student', 'subject', 'tariff')
        .order_by('-created_at')
    )
    active = [s for s in subs if s.status in Subscription.ACTIVE_STATUSES]
    history = [s for s in subs if s.status not in Subscription.ACTIVE_STATUSES]

    # Доход учителя — суммируем LESSON_PAYOUT transactions из его кошелька.
    payout_filter = dict(
        wallet__user=teacher.user,
        type=Transaction.Type.LESSON_PAYOUT,
        status=Transaction.Status.COMPLETED,
    )
    total_earned = (
        Transaction.objects.filter(**payout_filter)
        .aggregate(s=Sum('amount'))['s']
    ) or 0
    last_30_days = timezone.now() - timedelta(days=30)
    earned_30d = (
        Transaction.objects.filter(**payout_filter, created_at__gte=last_30_days)
        .aggregate(s=Sum('amount'))['s']
    ) or 0
    # Сколько денег ещё «висит» в эскроу по моим подпискам — потенциальный доход.
    pending_escrow = sum(
        s.escrow_balance * (1 - s.commission_rate) for s in active
    )

    return render(request, 'billing/teacher_subscribers.html', {
        'active': active,
        'history': history,
        'total_earned': total_earned,
        'earned_30d': earned_30d,
        'pending_escrow': pending_escrow,
    })


# ---------- Homework (LMS, Phase 8) ---------------------------------------


def _user_role_for_homework(request, homework):
    """Возвращает 'teacher' / 'student' / 'admin' / None — кто это к ДЗ."""
    if request.user.is_staff:
        return 'admin'
    if homework.teacher.user_id == request.user.id:
        return 'teacher'
    if homework.student_id == request.user.id:
        return 'student'
    return None


@login_required
def teacher_homework_list(request):
    """Все задания, которые учитель раздал по своим подпискам."""
    teacher = _get_teacher_or_403(request)
    if teacher is None:
        return redirect('home')

    homeworks = (
        Homework.objects.filter(teacher=teacher)
        .select_related('student', 'subscription__subject', 'submission')
        .order_by('-created_at')
    )
    pending = [h for h in homeworks if h.status == Homework.Status.SUBMITTED]
    others = [h for h in homeworks if h.status != Homework.Status.SUBMITTED]
    return render(request, 'billing/homework_teacher_list.html', {
        'pending': pending,
        'others': others,
    })


@login_required
def teacher_homework_create(request):
    """Учитель создаёт ДЗ для одного из своих активных подписчиков."""
    teacher = _get_teacher_or_403(request)
    if teacher is None:
        return redirect('home')

    active_subs = (
        Subscription.objects
        .filter(teacher=teacher, status__in=Subscription.ACTIVE_STATUSES)
        .select_related('student', 'subject')
        .order_by('-created_at')
    )

    if request.method == 'POST':
        sub_id = request.POST.get('subscription')
        sub = active_subs.filter(pk=sub_id).first() if sub_id else None
        form = HomeworkForm(request.POST)
        if sub is None:
            messages.error(request, 'Выберите активную подписку из списка.')
        elif form.is_valid():
            # Файлы — валидация перед сохранением.
            files = request.FILES.getlist('attachments')
            errors = []
            for f in files:
                try:
                    validate_homework_file(f)
                except Exception as e:
                    errors.append(f'{f.name}: {e}')
            if errors:
                for e in errors:
                    messages.error(request, e)
            else:
                hw = form.save(commit=False)
                hw.subscription = sub
                hw.teacher = teacher
                hw.student = sub.student
                hw.save()
                for f in files:
                    HomeworkAttachment.objects.create(
                        homework=hw, file=f, filename=f.name,
                        file_size=f.size, mime_type=getattr(f, 'content_type', '') or '',
                    )
                messages.success(request, f'Задание «{hw.title}» назначено ученику.')
                return redirect('teacher_homework_list')
    else:
        form = HomeworkForm()

    return render(request, 'billing/homework_create.html', {
        'form': form,
        'active_subs': active_subs,
    })


@login_required
def homework_detail(request, hw_id):
    """Единая страница ДЗ — рендерим разный UI для учителя и ученика."""
    homework = get_object_or_404(
        Homework.objects
        .select_related('teacher__user', 'student', 'subscription__subject',
                         'submission'),
        pk=hw_id,
    )
    role = _user_role_for_homework(request, homework)
    if role is None:
        messages.error(request, 'Доступ к этому заданию только у участников подписки.')
        return redirect('home')

    submission = getattr(homework, 'submission', None)

    # POST-ветки (только для соответствующих ролей)
    if request.method == 'POST':
        if role == 'student':
            return _handle_student_submit(request, homework, submission)
        if role == 'teacher':
            return _handle_teacher_grade(request, homework, submission)

    submission_form = HomeworkSubmissionForm(instance=submission) if role == 'student' else None
    grade_form = HomeworkGradeForm() if role == 'teacher' else None

    return render(request, 'billing/homework_detail.html', {
        'homework': homework,
        'submission': submission,
        'role': role,
        'submission_form': submission_form,
        'grade_form': grade_form,
    })


def _handle_student_submit(request, homework, submission):
    """Ученик сдаёт работу (или пересдаёт, если status=returned)."""
    if homework.status not in (Homework.Status.ASSIGNED, Homework.Status.RETURNED):
        messages.warning(request, 'Это задание уже сдано и не может быть изменено.')
        return redirect('homework_detail', hw_id=homework.id)

    form = HomeworkSubmissionForm(request.POST, instance=submission)
    files = request.FILES.getlist('files')

    # Должно быть хоть что-то (текст или файл)
    if not (form.data.get('text_response', '').strip() or files):
        messages.error(request, 'Напишите ответ или прикрепите хотя бы один файл.')
        return redirect('homework_detail', hw_id=homework.id)

    # Валидация файлов
    for f in files:
        try:
            validate_homework_file(f)
        except Exception as e:
            messages.error(request, f'{f.name}: {e}')
            return redirect('homework_detail', hw_id=homework.id)

    if form.is_valid():
        if submission is None:
            submission = form.save(commit=False)
            submission.homework = homework
            submission.student = request.user
            submission.save()
        else:
            form.save()
        # Новые файлы добавляем (старые остаются — могут уже быть на доработке).
        for f in files:
            HomeworkSubmissionFile.objects.create(
                submission=submission, file=f, filename=f.name,
                file_size=f.size, mime_type=getattr(f, 'content_type', '') or '',
            )
        homework.status = Homework.Status.SUBMITTED
        homework.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Работа отправлена учителю.')
    return redirect('homework_detail', hw_id=homework.id)


def _handle_teacher_grade(request, homework, submission):
    """Учитель ставит оценку или возвращает на доработку."""
    if submission is None:
        messages.error(request, 'Ученик ещё не сдал работу.')
        return redirect('homework_detail', hw_id=homework.id)
    if homework.status not in (Homework.Status.SUBMITTED, Homework.Status.GRADED):
        messages.error(request, 'Это задание нельзя оценить.')
        return redirect('homework_detail', hw_id=homework.id)

    form = HomeworkGradeForm(request.POST)
    if not form.is_valid():
        # Перерисовываем ту же страницу с ошибками
        return render(request, 'billing/homework_detail.html', {
            'homework': homework, 'submission': submission, 'role': 'teacher',
            'grade_form': form,
        })

    decision = form.cleaned_data['decision']
    feedback = (form.cleaned_data.get('feedback') or '').strip()
    if decision == HomeworkGradeForm.DECISION_RETURN:
        homework.status = Homework.Status.RETURNED
        homework.save(update_fields=['status', 'updated_at'])
        submission.feedback = feedback
        submission.grade = None
        submission.save(update_fields=['feedback', 'grade', 'updated_at'])
        messages.success(request, 'Работа возвращена ученику на доработку.')
    else:
        from django.utils import timezone
        submission.grade = form.cleaned_data['grade']
        submission.feedback = feedback
        submission.graded_at = timezone.now()
        submission.save(update_fields=['grade', 'feedback', 'graded_at', 'updated_at'])
        homework.status = Homework.Status.GRADED
        homework.save(update_fields=['status', 'updated_at'])
        messages.success(request, f'Оценка {submission.grade} проставлена.')
    return redirect('homework_detail', hw_id=homework.id)


@login_required
def student_homework_list(request):
    """Все задания текущего ученика."""
    homeworks = (
        Homework.objects.filter(student=request.user)
        .select_related('teacher__user', 'subscription__subject', 'submission')
        .order_by('-created_at')
    )
    pending = [h for h in homeworks if h.status in (Homework.Status.ASSIGNED, Homework.Status.RETURNED)]
    finished = [h for h in homeworks if h.status in (Homework.Status.SUBMITTED, Homework.Status.GRADED)]
    return render(request, 'billing/homework_student_list.html', {
        'pending': pending,
        'finished': finished,
    })


# ---------- Progress (Phase 9) -------------------------------------------


@login_required
def my_progress(request):
    """Прогресс ученика — сводка по всем активным подпискам."""
    # prefetch bookings__slot и homeworks → свойства прогресса считаются из кэша
    # (0 доп. запросов на подписку вместо ~8 N+1).
    subs = list(
        Subscription.objects
        .filter(student=request.user, status__in=Subscription.ACTIVE_STATUSES)
        .select_related('teacher__user', 'subject')
        .prefetch_related('bookings__slot', 'homeworks')
        .order_by('-created_at')
    )
    history_subs = list(
        Subscription.objects
        .filter(student=request.user)
        .exclude(status__in=Subscription.ACTIVE_STATUSES)
        .select_related('teacher__user', 'subject')
        .prefetch_related('bookings__slot', 'homeworks')
        .order_by('-created_at')[:10]
    )
    # Общая статистика по всем подпискам
    from teachers.models import Booking
    total_completed_lessons = Booking.objects.filter(
        student=request.user, status='completed',
    ).count()
    all_subs = subs + history_subs
    total_homework = sum(s.homework_total for s in all_subs)
    total_hw_graded = sum(s.homework_graded for s in all_subs)

    return render(request, 'billing/student_progress.html', {
        'subs': subs,
        'history_subs': history_subs,
        'total_completed_lessons': total_completed_lessons,
        'total_homework': total_homework,
        'total_hw_graded': total_hw_graded,
    })


@login_required
def dashboard(request):
    """Точка входа: роутит на student/teacher dashboard по user_type."""
    if request.user.user_type == 'teacher':
        return teacher_dashboard(request)
    return student_dashboard(request)


def student_dashboard(request):
    """Сводка для ученика: уроки сегодня/завтра, ДЗ, подписки, прогресс, кошелёк."""
    from datetime import timedelta
    from decimal import Decimal
    from django.db.models import Avg, Count, Q as DjQ, Sum
    from django.utils import timezone
    from teachers.models import Booking, StudentProfile
    from .models import Wallet

    try:
        student_profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        return redirect('profile')

    now = timezone.now()
    today_end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = now + timedelta(days=7)

    wallet, _ = Wallet.objects.get_or_create(user=request.user)

    # Ближайшие уроки (confirmed, в будущем, в ближайшие 7 дней)
    upcoming = (
        Booking.objects
        .filter(student=request.user, status='confirmed', slot__start_at__gte=now,
                slot__start_at__lte=week_end)
        .select_related('slot__teacher__user', 'subject', 'subscription')
        .order_by('slot__start_at')[:5]
    )
    lessons_today = (
        Booking.objects
        .filter(student=request.user, status='confirmed',
                slot__start_at__gte=now, slot__start_at__lt=today_end)
        .count()
    )

    # Активные подписки
    active_subs = (
        Subscription.objects
        .filter(student=request.user, status__in=Subscription.ACTIVE_STATUSES)
        .select_related('teacher__user', 'subject')
        .order_by('-created_at')
    )

    # ДЗ — pending (новые) и submitted (на проверке)
    new_homework = (
        Homework.objects.filter(student=request.user, status=Homework.Status.ASSIGNED)
        .select_related('teacher__user', 'subscription__subject')
        .order_by('-created_at')[:5]
    )
    pending_grade_hw = (
        Homework.objects.filter(student=request.user, status=Homework.Status.SUBMITTED)
        .select_related('teacher__user', 'subscription__subject')
        .order_by('-created_at')[:5]
    )

    # Сводные метрики
    total_lessons_done = Booking.objects.filter(
        student=request.user, status='completed',
    ).count()
    total_subs = Subscription.objects.filter(student=request.user).count()

    # Средняя оценка по всем проверенным ДЗ
    avg_grade_qs = HomeworkSubmission.objects.filter(
        student=request.user, grade__isnull=False,
    ).aggregate(avg=Avg('grade'))
    avg_grade = avg_grade_qs['avg']
    if avg_grade is not None:
        avg_grade = round(float(avg_grade), 1)

    # Часы изучено: сумма длительностей completed-уроков
    # Часы изучено — одним агрегатом в БД (а не загрузкой всех уроков в Python).
    from django.db.models import DurationField, ExpressionWrapper, F
    _dur = Booking.objects.filter(
        student=request.user, status='completed',
    ).aggregate(total=Sum(ExpressionWrapper(
        F('slot__end_at') - F('slot__start_at'), output_field=DurationField(),
    )))['total']
    hours_studied = round(_dur.total_seconds() / 3600, 1) if _dur else 0.0

    # Последние транзакции (3 шт)
    recent_tx = (
        Transaction.objects.filter(wallet=wallet)
        .order_by('-created_at')[:5]
    )

    # Phase 10.5: Conversion funnel — пробные, прошедшие за последние 30 дней,
    # по которым ученик ещё НЕ подписался к этому учителю по этому предмету.
    cutoff = now - timedelta(days=30)
    completed_trials = (
        Booking.objects
        .filter(
            student=request.user,
            is_trial=True,
            status='completed',
            slot__end_at__gte=cutoff,
            slot__end_at__lt=now,  # действительно завершён
        )
        .select_related('slot__teacher__user', 'subject')
        .order_by('-slot__end_at')
    )
    # Батчим: вместо 2 запросов на каждый пробный — 2 запроса на всех.
    from collections import defaultdict
    trials = list(completed_trials[:12])
    pairs = {(t.slot.teacher_id, t.subject_id) for t in trials
             if t.slot.teacher_id and t.subject_id}
    # (teacher, subject), по которым уже есть активная подписка — одним запросом.
    subscribed_pairs = set(
        Subscription.objects.filter(
            student=request.user, status__in=Subscription.ACTIVE_STATUSES,
        ).values_list('teacher_id', 'subject_id')
    )
    # Тарифы для всех нужных учителей/предметов — одним запросом.
    tariffs_by_pair = defaultdict(list)
    if pairs:
        t_ids = {p[0] for p in pairs}
        s_ids = {p[1] for p in pairs}
        for t in (Tariff.objects.filter(teacher_id__in=t_ids, subject_id__in=s_ids, is_active=True)
                  .order_by('lessons_per_week', 'duration_months')):
            tariffs_by_pair[(t.teacher_id, t.subject_id)].append(t)

    recent_trials_to_convert = []
    seen = set()
    for b in trials:
        key = (b.slot.teacher_id, b.subject_id)
        if not key[0] or not key[1] or key in subscribed_pairs or key in seen:
            continue
        seen.add(key)
        delta_h = int((now - b.slot.end_at).total_seconds() / 3600)
        recent_trials_to_convert.append({
            'booking': b,
            'teacher': b.slot.teacher,
            'subject': b.subject,
            'tariffs': tariffs_by_pair.get(key, [])[:3],
            'hours_since': delta_h,
            'days_since': delta_h // 24,
        })
        if len(recent_trials_to_convert) >= 3:
            break

    return render(request, 'billing/student_dashboard.html', {
        'wallet': wallet,
        'upcoming': upcoming,
        'lessons_today': lessons_today,
        'active_subs': active_subs,
        'new_homework': new_homework,
        'pending_grade_hw': pending_grade_hw,
        'total_lessons_done': total_lessons_done,
        'total_subs': total_subs,
        'avg_grade': avg_grade,
        'hours_studied': hours_studied,
        'recent_tx': recent_tx,
        'recent_trials_to_convert': recent_trials_to_convert,
    })


def teacher_dashboard(request):
    """Сводка для учителя: сегодня, заработок, ДЗ на проверку, ученики."""
    from datetime import timedelta
    from decimal import Decimal
    from django.db.models import Count, Q as DjQ, Sum
    from django.utils import timezone
    from teachers.models import Booking
    from .models import Wallet

    teacher = _get_teacher_or_403(request)
    if teacher is None:
        return redirect('home')

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)

    wallet, _ = Wallet.objects.get_or_create(user=request.user)

    # Сегодняшние уроки
    today_lessons = (
        Booking.objects
        .filter(slot__teacher=teacher, status__in=['confirmed', 'completed'],
                slot__start_at__gte=today_start, slot__start_at__lt=today_end)
        .select_related('student', 'subject', 'slot')
        .order_by('slot__start_at')
    )

    # На этой неделе
    week_lessons_count = Booking.objects.filter(
        slot__teacher=teacher, status__in=['confirmed', 'completed'],
        slot__start_at__gte=week_start, slot__start_at__lt=week_start + timedelta(days=7),
    ).count()

    # Pending: bookings, требующие подтверждения
    pending_bookings = (
        Booking.objects
        .filter(slot__teacher=teacher, status='pending')
        .select_related('student', 'subject', 'slot')
        .order_by('expires_at')[:5]
    )

    # ДЗ на проверку (сортируем по submitted_at из submission)
    homework_to_grade = (
        Homework.objects.filter(teacher=teacher, status=Homework.Status.SUBMITTED)
        .select_related('student', 'subscription__subject', 'submission')
        .order_by('-submission__submitted_at')[:5]
    )

    # Активные ученики (по подпискам)
    active_students_count = Subscription.objects.filter(
        teacher=teacher, status__in=Subscription.ACTIVE_STATUSES,
    ).values('student').distinct().count()

    # Заработок: today / week / month / total
    payouts_qs = Transaction.objects.filter(
        wallet=wallet, type=Transaction.Type.LESSON_PAYOUT,
    )
    earned_today = payouts_qs.filter(created_at__gte=today_start).aggregate(
        s=Sum('amount'))['s'] or Decimal('0')
    earned_week = payouts_qs.filter(created_at__gte=week_start).aggregate(
        s=Sum('amount'))['s'] or Decimal('0')
    earned_month = payouts_qs.filter(created_at__gte=month_start).aggregate(
        s=Sum('amount'))['s'] or Decimal('0')
    earned_total = payouts_qs.aggregate(s=Sum('amount'))['s'] or Decimal('0')

    # Эскроу — деньги, которые ждут payout
    escrow_total = Subscription.objects.filter(
        teacher=teacher, status__in=Subscription.ACTIVE_STATUSES,
    ).aggregate(s=Sum('escrow_balance'))['s'] or Decimal('0')

    # Последние транзакции
    recent_tx = (
        Transaction.objects.filter(wallet=wallet)
        .order_by('-created_at')[:5]
    )

    return render(request, 'billing/teacher_dashboard.html', {
        'teacher': teacher,
        'wallet': wallet,
        'today_lessons': today_lessons,
        'week_lessons_count': week_lessons_count,
        'pending_bookings': pending_bookings,
        'homework_to_grade': homework_to_grade,
        'active_students_count': active_students_count,
        'earned_today': earned_today,
        'earned_week': earned_week,
        'earned_month': earned_month,
        'earned_total': earned_total,
        'escrow_total': escrow_total,
        'recent_tx': recent_tx,
    })


@login_required
def teacher_student_progress(request, sub_id):
    """Учитель просматривает прогресс конкретной подписки ученика."""
    teacher = _get_teacher_or_403(request)
    if teacher is None:
        return redirect('home')

    sub = get_object_or_404(
        Subscription.objects.select_related('student', 'subject').prefetch_related('bookings__slot'),
        pk=sub_id, teacher=teacher,
    )

    # Все уроки подписки в хронологическом порядке
    from teachers.models import Booking
    lessons = (
        sub.bookings.select_related('slot', 'review')
        .order_by('slot__start_at')
    )

    # Все ДЗ подписки
    homeworks = sub.homeworks.select_related('submission').order_by('-created_at')

    return render(request, 'billing/teacher_student_progress.html', {
        'sub': sub,
        'lessons': lessons,
        'homeworks': homeworks,
    })
