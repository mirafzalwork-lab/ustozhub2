from __future__ import annotations

import json
import logging
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from teachers.models import TeacherProfile

from .forms import HomeworkForm, HomeworkGradeForm, HomeworkSubmissionForm, TariffForm, WithdrawalRequestForm
from .models import (
    DismissedTrialSuggestion,
    Homework, HomeworkAttachment, HomeworkSubmission, HomeworkSubmissionFile,
    MulticardInvoice,
    Subscription, Tariff, Transaction, WithdrawalRequest,
)
from .multicard import (
    MulticardClient,
    MulticardError,
    build_topup_ofd,
    sum_to_tiyin,
    verify_sign,
)
from .validators import validate_homework_file
from .services import (
    AlreadySubscribed,
    CancellationError,
    InsufficientFunds,
    SubscriptionService,
    WalletService,
    WithdrawalError,
    WithdrawalService,
)

logger = logging.getLogger(__name__)


def _get_teacher_or_403(request):
    """Возвращает TeacherProfile текущего пользователя или редиректит на home."""
    try:
        return request.user.teacher_profile
    except TeacherProfile.DoesNotExist:
        messages.error(request, _('Эта страница доступна только учителям.'))
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
            messages.success(request, _('Тариф «%(name)s» создан.') % {'name': tariff.name or tariff.subject})
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
            messages.success(request, _('Тариф обновлён.'))
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
    messages.success(request, _('Тариф удалён.'))
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
    state = _('включён') if tariff.is_active else _('выключен')
    messages.success(request, _('Тариф %(state)s.') % {'state': state})
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
        'multicard_enabled': getattr(settings, 'MULTICARD_ENABLED', False),
        'min_topup': getattr(settings, 'MULTICARD_MIN_TOPUP', 1000),
        'topup_configured': bool(getattr(settings, 'TOPUP_CARD_NUMBER', '')),
        'card_number': getattr(settings, 'TOPUP_CARD_NUMBER', ''),
        'card_holder': getattr(settings, 'TOPUP_CARD_HOLDER', ''),
        'bank_name': getattr(settings, 'TOPUP_BANK_NAME', ''),
        'telegram_handle': getattr(settings, 'TOPUP_TELEGRAM_HANDLE', ''),
        'support_phone': getattr(settings, 'TOPUP_SUPPORT_PHONE', ''),
        'processing_hours': getattr(settings, 'TOPUP_PROCESSING_HOURS', '1-2'),
    })


# ---------- Multicard: онлайн-пополнение кошелька -------------------------


def _absolute_url(path: str) -> str:
    """SITE_URL + path. Multicard требует публичный HTTPS-URL для callback."""
    return f"{settings.SITE_URL.rstrip('/')}{path}"


@login_required
@require_POST
def wallet_topup_multicard(request):
    """Создать инвойс Multicard и отправить пользователя на checkout_url."""
    if not getattr(settings, 'MULTICARD_ENABLED', False):
        messages.error(request, _('Онлайн-оплата временно недоступна.'))
        return redirect('wallet_topup_request')

    try:
        amount = int(float(request.POST.get('amount') or 0))
    except (TypeError, ValueError):
        amount = 0

    next_url = request.POST.get('next', '')
    if next_url and not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = ''
    back = f"{reverse('wallet_topup_request')}?next={next_url}" if next_url else reverse('wallet_topup_request')

    if amount < settings.MULTICARD_MIN_TOPUP:
        messages.error(request, _('Минимальная сумма пополнения — %(min)s сум.') % {
            'min': settings.MULTICARD_MIN_TOPUP,
        })
        return redirect(back)
    if amount > settings.MULTICARD_MAX_TOPUP:
        messages.error(request, _('Максимальная сумма пополнения — %(max)s сум.') % {
            'max': settings.MULTICARD_MAX_TOPUP,
        })
        return redirect(back)

    invoice = MulticardInvoice.objects.create(
        user=request.user,
        amount=amount,
        store_id=settings.MULTICARD_STORE_ID,
    )
    amount_tiyin = sum_to_tiyin(amount)
    return_path = f"{reverse('wallet_topup_return')}?invoice={invoice.id}"
    if next_url:
        return_path += f'&next={next_url}'

    try:
        client = MulticardClient()
        data = client.create_invoice(
            store_id=settings.MULTICARD_STORE_ID,
            amount_tiyin=amount_tiyin,
            invoice_id=str(invoice.id),
            callback_url=_absolute_url(reverse('multicard_callback')),
            ofd=build_topup_ofd(amount_tiyin),
            return_url=_absolute_url(return_path),
            lang=(request.LANGUAGE_CODE or 'ru')[:2],
        )
    except MulticardError as exc:
        logger.error('Multicard create_invoice failed for invoice %s: %s', invoice.id, exc)
        invoice.status = MulticardInvoice.Status.ERROR
        invoice.save(update_fields=['status', 'updated_at'])
        messages.error(request, _('Не удалось создать платёж. Попробуйте позже или другой способ.'))
        return redirect(back)

    checkout_url = data.get('checkout_url')
    invoice.multicard_uuid = data.get('uuid', '')
    invoice.checkout_url = checkout_url or ''
    invoice.short_link = data.get('short_link', '')
    invoice.save(update_fields=['multicard_uuid', 'checkout_url', 'short_link', 'updated_at'])

    if not checkout_url:
        messages.error(request, _('Платёжный шлюз не вернул ссылку на оплату. Попробуйте позже.'))
        return redirect(back)

    return redirect(checkout_url)


def _credit_invoice(invoice: MulticardInvoice, payload: dict) -> None:
    """Идемпотентно зачислить успешный платёж в кошелёк и закрыть инвойс."""
    with db_transaction.atomic():
        inv = MulticardInvoice.objects.select_for_update().get(pk=invoice.pk)
        if inv.status == MulticardInvoice.Status.SUCCESS and inv.transaction_id:
            return  # уже зачислено
        tx = WalletService.credit(
            user=inv.user,
            amount=inv.amount,
            tx_type=Transaction.Type.DEPOSIT,
            idempotency_key=f'multicard:{inv.id}',
            description=_('Пополнение кошелька через Multicard'),
            reference=inv.multicard_uuid or str(inv.id),
        )
        inv.status = MulticardInvoice.Status.SUCCESS
        inv.transaction = tx
        inv.paid_at = timezone.now()
        inv.card_pan = payload.get('card_pan', '') or inv.card_pan
        inv.ps = payload.get('ps', '') or inv.ps
        inv.receipt_url = payload.get('receipt_url', '') or inv.receipt_url
        inv.raw_callback = payload
        inv.save(update_fields=[
            'status', 'transaction', 'paid_at', 'card_pan', 'ps',
            'receipt_url', 'raw_callback', 'updated_at',
        ])


@csrf_exempt
@require_POST
def multicard_callback(request):
    """Webhook Multicard. Проверяет подпись, зачисляет DEPOSIT идемпотентно.

    Должен отвечать 2xx — иначе Multicard повторяет до 5 раз.
    """
    # Опциональный whitelisting по IP (X-Forwarded-For за reverse-proxy).
    allowed_ip = getattr(settings, 'MULTICARD_CALLBACK_IP', '')
    if allowed_ip:
        xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
        remote = (xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR', ''))
        if remote and remote != allowed_ip:
            logger.warning('Multicard callback с неожиданного IP: %s', remote)
            # Не блокируем жёстко (балансировщики/прокси), но логируем.

    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        payload = request.POST.dict()

    uuid_ = payload.get('uuid', '')
    invoice_id = payload.get('invoice_id', '')
    amount = payload.get('amount', '')
    status = payload.get('status', '')
    sign = payload.get('sign', '')
    # store_id из callback — числовой (напр. 6), участвует в подписи.
    callback_store_id = payload.get('store_id', '')

    if not verify_sign(callback_store_id, invoice_id, amount, sign):
        logger.warning(
            'Multicard callback: неверная подпись (invoice_id=%s, store_id=%s)',
            invoice_id, callback_store_id,
        )
        return JsonResponse({'success': False, 'error': 'invalid sign'}, status=400)

    try:
        invoice = MulticardInvoice.objects.get(pk=invoice_id)
    except (MulticardInvoice.DoesNotExist, ValueError, ValidationError):
        logger.warning('Multicard callback: инвойс не найден (invoice_id=%s)', invoice_id)
        return JsonResponse({'success': False, 'error': 'invoice not found'}, status=404)

    # Сумма из callback (тийины) должна совпадать с заявленной.
    try:
        if int(amount) != sum_to_tiyin(invoice.amount):
            logger.error(
                'Multicard callback: сумма не совпала invoice=%s callback=%s expected=%s',
                invoice.id, amount, sum_to_tiyin(invoice.amount),
            )
            return JsonResponse({'success': False, 'error': 'amount mismatch'}, status=400)
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'bad amount'}, status=400)

    if not invoice.multicard_uuid and uuid_:
        invoice.multicard_uuid = uuid_
        invoice.save(update_fields=['multicard_uuid', 'updated_at'])

    # Multicard шлёт ДВА вида callback:
    #   * «callback-success» — после успешного списания, БЕЗ поля status,
    #     но с payment_time / card_pan / receipt_url;
    #   * «webhook» — при смене статуса, с явным полем status
    #     (draft/progress/success/error/revert/hold).
    is_success = (
        status == MulticardInvoice.Status.SUCCESS
        or (not status and bool(payload.get('payment_time')))
    )

    if is_success:
        _credit_invoice(invoice, payload)
    elif status in (MulticardInvoice.Status.ERROR, MulticardInvoice.Status.REVERT):
        # Не трогаем уже зачисленный инвойс (revert обрабатывается отдельно вручную).
        if invoice.status != MulticardInvoice.Status.SUCCESS:
            invoice.status = status
            invoice.raw_callback = payload
            invoice.save(update_fields=['status', 'raw_callback', 'updated_at'])
    # Подтверждаем 200, чтобы Multicard не ретраил и не отменял платёж.
    return JsonResponse({'success': True})


@login_required
def wallet_topup_return(request):
    """Страница возврата после оплаты на стороне Multicard (return_url)."""
    invoice_id = request.GET.get('invoice', '')
    invoice = None
    if invoice_id:
        try:
            invoice = MulticardInvoice.objects.filter(
                pk=invoice_id, user=request.user
            ).first()
        except (ValueError, ValidationError):
            invoice = None

    # Callback мог ещё не прийти — подтянем статус напрямую (best-effort).
    if invoice and invoice.status not in (
        MulticardInvoice.Status.SUCCESS, MulticardInvoice.Status.ERROR,
    ) and invoice.multicard_uuid:
        try:
            data = MulticardClient().get_payment(invoice.multicard_uuid)
            if data.get('status') == MulticardInvoice.Status.SUCCESS:
                _credit_invoice(invoice, data)
                invoice.refresh_from_db()
        except MulticardError as exc:
            logger.info('Multicard get_payment при возврате не удался: %s', exc)

    next_url = request.GET.get('next', '')
    if next_url and not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = ''

    return render(request, 'billing/topup_return.html', {
        'invoice': invoice,
        'wallet': request.user.wallet,
        'next_url': next_url,
        'is_success': bool(invoice and invoice.status == MulticardInvoice.Status.SUCCESS),
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
        messages.error(request, _('Только ученик может оформить обучение.'))
        return redirect('teacher_detail', id=teacher.id)
    if teacher.user_id == request.user.id:
        messages.error(request, _('Нельзя оформить обучение у самого себя.'))
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
        messages.error(request, _('У учителя не указаны предметы.'))
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
                    raise ValueError(_('Выберите тариф.'))
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
                _('Заявка отправлена учителю. Мы уведомим вас, когда её подтвердят.'),
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
            messages.success(request, _('Заявка подтверждена. Ученик получит уведомление об оплате.'))
        elif action == 'reject':
            SubscriptionService.reject_request(sub, reason=(request.POST.get('reason') or '').strip())
            messages.success(request, _('Заявка отклонена.'))
        else:
            messages.error(request, _('Неизвестное действие.'))
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
        messages.info(request, _('Оплата недоступна: статус «%(status)s».') % {'status': sub.get_status_display()})
        return redirect('my_subscriptions')

    wallet = request.user.wallet
    has_enough = wallet.balance >= sub.price_total
    needed_amount = max(int(sub.price_total - wallet.balance), 0)

    if request.method == 'POST':
        try:
            SubscriptionService.pay(sub, idempotency_key=request.POST.get('idempotency_key') or '')
            messages.success(request, _('Оплата прошла! Теперь выберите удобное расписание.'))
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
        messages.info(request, _('Расписание доступно только для оплаченной подписки.'))
        return redirect('my_subscriptions')
    # Возвращённые ученику уроки (ТЗ §6/§8) не занимают квоту: прощённая
    # неявка и «не состоялся» дают право выбрать новую дату.
    _active_bk = sub.bookings.exclude(
        status__in=['cancelled_by_student', 'cancelled_by_teacher', 'not_held']
    ).exclude(no_show_forgiven=True)
    booked_count = _active_bk.count()
    if booked_count >= sub.total_lessons:
        messages.info(request, _('Все уроки уже забронированы.'))
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
                _('Выберите ровно %(required)s занятия в неделю (выбрано %(selected)s).') % {
                    'required': sub.lessons_per_week, 'selected': len(pattern)},
            )
        else:
            try:
                created = SubscriptionService.book_schedule(sub, pattern)
                total_booked = sub.bookings.exclude(
                    status__in=['cancelled_by_student', 'cancelled_by_teacher', 'not_held']
                ).exclude(no_show_forgiven=True).count()
                if total_booked >= sub.total_lessons:
                    messages.success(
                        request,
                        _('Расписание сформировано: забронировано %(count)s уроков.') % {'count': len(created)},
                    )
                    return redirect('my_bookings_page')
                # Частично: свободных слотов учителя не хватило на весь объём.
                messages.success(
                    request,
                    _('Забронировано ещё %(count)s уроков по свободным слотам учителя '
                      '(%(booked)s из %(total)s).') % {
                        'count': len(created), 'booked': total_booked, 'total': sub.total_lessons},
                )
                messages.info(
                    request,
                    _('Остальные уроки можно добрать здесь же, когда учитель откроет новые '
                      'слоты в календаре — напишите ему с просьбой добавить время.'),
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
            messages.error(request, _('Опишите проблему подробнее (минимум 10 символов).'))
        else:
            try:
                DisputeService.open(booking, student=request.user, reason=reason)
                messages.success(
                    request,
                    _('Спор открыт. Администрация рассмотрит его; выплата учителю заморожена.'),
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
        messages.success(request, _('Спор отозван.'))
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
        messages.error(request, _('У вас нет прав отменить эту подписку.'))
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
        _('Подписка отменена. Возвращено на баланс: %(refunded)s сум. '
          'Отменено уроков: %(cancelled)s.') % {
            'refunded': int(refunded), 'cancelled': result['cancelled_bookings']}
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
        messages.error(request, _('У вас нет прав приостановить эту подписку.'))
        return redirect('my_subscriptions')
    reason = (request.POST.get('reason') or '').strip()
    try:
        freed = SubscriptionService.pause(sub, reason=reason)
        messages.success(
            request,
            _('Подписка приостановлена. Снято будущих уроков: %(freed)s. '
              'Возобновите в любой момент — срок продлится на время паузы.') % {'freed': freed}
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
        messages.error(request, _('У вас нет прав возобновить эту подписку.'))
        return redirect('my_subscriptions')
    try:
        created = SubscriptionService.resume(sub)
        messages.success(
            request,
            _('Подписка возобновлена. Запланировано уроков: %(created)s.') % {'created': created}
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
                    _('Заявка на вывод %(amount)s сум создана. Ожидайте подтверждения.') % {'amount': int(wr.amount)}
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
        messages.success(request, _('Заявка отменена, %(amount)s сум возвращены на баланс.') % {'amount': int(wr.amount)})
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
            messages.error(request, _('Выберите активную подписку из списка.'))
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
                messages.success(request, _('Задание «%(title)s» назначено ученику.') % {'title': hw.title})
                return redirect('teacher_homework_list')
    else:
        form = HomeworkForm()

    # Предвыбор ученика: при переходе со страницы прогресса передаётся ?subscription=<uuid>.
    preselected_sub_id = request.GET.get('subscription') or request.POST.get('subscription') or ''

    return render(request, 'billing/homework_create.html', {
        'form': form,
        'active_subs': active_subs,
        'preselected_sub_id': str(preselected_sub_id),
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
        messages.error(request, _('Доступ к этому заданию только у участников подписки.'))
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
        messages.warning(request, _('Это задание уже сдано и не может быть изменено.'))
        return redirect('homework_detail', hw_id=homework.id)

    form = HomeworkSubmissionForm(request.POST, instance=submission)
    files = request.FILES.getlist('files')

    # Должно быть хоть что-то (текст или файл)
    if not (form.data.get('text_response', '').strip() or files):
        messages.error(request, _('Напишите ответ или прикрепите хотя бы один файл.'))
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
        messages.success(request, _('Работа отправлена учителю.'))
    return redirect('homework_detail', hw_id=homework.id)


def _handle_teacher_grade(request, homework, submission):
    """Учитель ставит оценку или возвращает на доработку."""
    if submission is None:
        messages.error(request, _('Ученик ещё не сдал работу.'))
        return redirect('homework_detail', hw_id=homework.id)
    if homework.status not in (Homework.Status.SUBMITTED, Homework.Status.GRADED):
        messages.error(request, _('Это задание нельзя оценить.'))
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
        messages.success(request, _('Работа возвращена ученику на доработку.'))
    else:
        from django.utils import timezone
        submission.grade = form.cleaned_data['grade']
        submission.feedback = feedback
        submission.graded_at = timezone.now()
        submission.save(update_fields=['grade', 'feedback', 'graded_at', 'updated_at'])
        homework.status = Homework.Status.GRADED
        homework.save(update_fields=['status', 'updated_at'])
        messages.success(request, _('Оценка %(grade)s проставлена.') % {'grade': submission.grade})
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
    # (teacher, subject), которые ученик скрыл вручную («Убрать» на карточке).
    dismissed_pairs = set(
        DismissedTrialSuggestion.objects.filter(
            student=request.user,
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
        if (not key[0] or not key[1] or key in subscribed_pairs
                or key in dismissed_pairs or key in seen):
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


@login_required
@require_POST
def dismiss_trial_suggestion(request, teacher_id):
    """Ученик скрывает карточку «Продолжить обучение» (учитель не понравился).

    Создаёт DismissedTrialSuggestion(student, teacher, subject) — после этого
    предложение по данной паре учитель/предмет больше не показывается в дашборде.
    """
    from django.http import JsonResponse
    try:
        subject_id = int(request.POST.get('subject') or 0)
    except (TypeError, ValueError):
        subject_id = 0
    if not subject_id:
        return JsonResponse({'success': False, 'error': 'subject required'}, status=400)

    teacher = get_object_or_404(TeacherProfile, pk=teacher_id)
    DismissedTrialSuggestion.objects.get_or_create(
        student=request.user, teacher=teacher, subject_id=subject_id,
    )
    return JsonResponse({'success': True})


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
