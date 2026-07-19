"""
Views для системы бронирования (Phase 2).

UI:
    GET  /teacher/calendar/              — страница календаря учителя

API (только для владельца профиля):
    GET    /api/calendar/slots/?start=&end=    список слотов в диапазоне (JSON)
    POST   /api/calendar/slots/                создать слот
    PATCH  /api/calendar/slots/<id>/           изменить (drag/resize)
    DELETE /api/calendar/slots/<id>/           удалить

Все endpoints требуют login + user_type='teacher'. Каждый учитель видит и
редактирует только свои слоты.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from .models import TeacherProfile, TimeSlot, Booking, SlotUnavailable, Subject, Review

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Permission decorators (расширение)
# --------------------------------------------------------------------------- #

def student_required(view):
    """Только аутентифицированный student."""
    @wraps(view)
    @login_required(login_url='login')
    def wrapper(request, *args, **kwargs):
        if request.user.user_type != 'student':
            return HttpResponseForbidden('Доступно только ученикам.')
        return view(request, *args, **kwargs)
    return wrapper


def authenticated_required(view):
    """Любой залогиненный пользователь (для my-bookings: и студент, и учитель)."""
    @wraps(view)
    @login_required(login_url='login')
    def wrapper(request, *args, **kwargs):
        return view(request, *args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def teacher_required(view):
    """Декоратор: пускает только аутентифицированного учителя с profile."""
    @wraps(view)
    @login_required(login_url='login')
    def wrapper(request, *args, **kwargs):
        if request.user.user_type != 'teacher':
            return HttpResponseForbidden('Доступно только учителям.')
        try:
            request.teacher_profile = request.user.teacher_profile
        except TeacherProfile.DoesNotExist:
            messages.warning(request, _('Сначала завершите профиль учителя.'))
            return redirect('teacher_register')
        return view(request, *args, **kwargs)
    return wrapper


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    # FullCalendar присылает '2026-05-19T10:00:00+03:00' и т.д.
    dt = parse_datetime(value)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _slot_to_event(slot: TimeSlot) -> dict:
    """Сериализация TimeSlot в формат FullCalendar Event."""
    colors = {
        'free':    {'bg': '#10B981', 'border': '#059669'},  # green
        'held':    {'bg': '#F59E0B', 'border': '#D97706'},  # amber
        'booked':  {'bg': '#4F46E5', 'border': '#4338CA'},  # indigo
        'blocked': {'bg': '#6B7280', 'border': '#4B5563'},  # gray
    }
    c = colors.get(slot.status, colors['free'])

    # Берём активный booking (pending/confirmed) — если есть.
    # Используем prefetched список (см. slots_list_api), либо fallback на запрос.
    from .models import Booking
    active_bookings = getattr(slot, '_active_bookings', None)
    if active_bookings is None:
        active = slot.bookings.filter(status__in=Booking.ACTIVE_STATUSES).first()
    else:
        active = active_bookings[0] if active_bookings else None

    booking_info = None
    if active:
        booking_info = {
            'id': str(active.id),
            'student_name': active.student.get_full_name() or active.student.username,
            'status': active.status,
            'status_display': active.get_status_display(),
            'is_trial': active.is_trial,
            'student_message': active.student_message[:200],
        }

    return {
        'id': slot.pk,
        'start': slot.start_at.isoformat(),
        'end': slot.end_at.isoformat(),
        'title': slot.get_status_display(),
        'backgroundColor': c['bg'],
        'borderColor': c['border'],
        'textColor': '#FFFFFF',
        'editable': slot.status in ('free', 'blocked'),
        'extendedProps': {
            'status': slot.status,
            'duration_minutes': slot.duration_minutes,
            'booking': booking_info,
        },
    }


def _json_error(message: str, status: int = 400, **extra):
    return JsonResponse({'error': message, **extra}, status=status)


_VALID_DAY_KEYS = {'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'}


def _validate_hhmm(value: str) -> str | None:
    """Принимает 'HH:MM'; возвращает нормализованную строку или None при ошибке."""
    if not isinstance(value, str):
        return None
    parts = value.strip().split(':')
    if len(parts) != 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return f'{h:02d}:{m:02d}'


def _normalize_schedule_payload(raw: dict) -> tuple[dict | None, str]:
    """
    Нормализует пришедший с фронта недельный шаблон.

    Вход: {"monday": [{"from": "09:00", "to": "12:00"}, ...], ...}
    Выход: (schedule_dict, '') в формате хранения weekly_schedule,
            либо (None, error_message) при ошибке валидации.

    Пустые/выходные дни просто опускаются. Интервалы проверяются:
    корректный HH:MM и from < to.
    """
    if not isinstance(raw, dict):
        return None, 'schedule должен быть объектом'
    result = {}
    for key, intervals in raw.items():
        if key not in _VALID_DAY_KEYS:
            return None, f'Неизвестный день: {key}'
        if not isinstance(intervals, list):
            return None, f'{key}: интервалы должны быть списком'
        clean = []
        for itv in intervals:
            if not isinstance(itv, dict):
                return None, f'{key}: некорректный интервал'
            f = _validate_hhmm(itv.get('from'))
            t = _validate_hhmm(itv.get('to'))
            if not f or not t:
                return None, f'{key}: время в формате ЧЧ:ММ'
            if f >= t:
                return None, f'{key}: начало интервала должно быть раньше конца'
            clean.append({'from': f, 'to': t})
        if clean:
            result[key] = clean
    return result, ''


# --------------------------------------------------------------------------- #
# UI page
# --------------------------------------------------------------------------- #

@teacher_required
def teacher_calendar(request):
    """Страница календаря учителя."""
    # Текущий шаблон недели в формате {day_key: [{"from","to"}, ...]} —
    # чтобы редактор расписания в модалке генерации был предзаполнен.
    intervals = request.teacher_profile.get_schedule_intervals()
    schedule_json = {
        key: [{'from': f, 'to': t} for f, t in intervals.get(key, [])]
        for key, _label in TeacherProfile.WEEKDAYS_ORDERED
    }
    return render(request, 'booking/teacher_calendar.html', {
        'teacher': request.teacher_profile,
        'schedule_json': json.dumps(schedule_json),
    })


# --------------------------------------------------------------------------- #
# API endpoints
# --------------------------------------------------------------------------- #

@teacher_required
@require_http_methods(['GET'])
def slots_list_api(request):
    """JSON со слотами учителя в указанном диапазоне (FullCalendar fetchEvents)."""
    start = _parse_iso(request.GET.get('start'))
    end = _parse_iso(request.GET.get('end'))
    if not start or not end:
        return _json_error('start и end (ISO datetime) обязательны')
    if start >= end:
        return _json_error('start должен быть раньше end')

    from django.db.models import Prefetch
    from .models import Booking
    active_qs = Booking.objects.filter(
        status__in=Booking.ACTIVE_STATUSES,
    ).select_related('student')
    slots = (
        TimeSlot.objects
        .filter(teacher=request.teacher_profile, start_at__gte=start, start_at__lt=end)
        .prefetch_related(Prefetch('bookings', queryset=active_qs, to_attr='_active_bookings'))
        .order_by('start_at')
    )
    return JsonResponse({'events': [_slot_to_event(s) for s in slots]})


@teacher_required
@require_http_methods(['POST'])
@transaction.atomic
def slots_create_api(request):
    """Создать новый слот. Body: {start, end, status?}.

    Транзакция атомарна — overlap-check и insert в одной транзакции,
    исключает race condition между двумя одновременными POST.
    """
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return _json_error('Invalid JSON')

    start = _parse_iso(data.get('start'))
    end = _parse_iso(data.get('end'))
    if not start or not end:
        return _json_error('start и end обязательны')
    if start >= end:
        return _json_error('start должен быть раньше end')
    if end - start > timezone.timedelta(hours=8):
        return _json_error('Длительность слота не может быть больше 8 часов')
    if start < timezone.now():
        return _json_error('Нельзя создать слот в прошлом')

    status = data.get('status', 'free')
    if status not in ('free', 'blocked'):
        return _json_error('status должен быть free или blocked')

    # Проверяем пересечение (lock на пересекающиеся строки этого учителя)
    overlap = TimeSlot.objects.select_for_update().filter(
        teacher=request.teacher_profile,
        start_at__lt=end,
        end_at__gt=start,
    ).exists()
    if overlap:
        return _json_error('Слот пересекается с существующим', status=409)

    try:
        # Savepoint: на PostgreSQL exclusion-констрейнт (миграция 0048) ловит
        # гонку двух параллельных POST, которую select_for_update не закрывает
        # (лочатся существующие строки, а не «отсутствие» пересечений).
        with transaction.atomic():
            slot = TimeSlot.objects.create(
                teacher=request.teacher_profile,
                start_at=start,
                end_at=end,
                status=status,
            )
    except IntegrityError:
        return _json_error('Слот пересекается с существующим', status=409)
    logger.info(f'Slot created: teacher={request.teacher_profile.pk} slot={slot.pk}')
    return JsonResponse({'event': _slot_to_event(slot)}, status=201)


@teacher_required
@require_http_methods(['PATCH', 'DELETE'])
@transaction.atomic
def slots_detail_api(request, slot_id: int):
    """Изменить (drag/resize) или удалить слот.

    Всё тело view — в единой транзакции. select_for_update требует
    активной транзакции на PostgreSQL (на SQLite — мягче, но единая
    логика проще и безопаснее).
    """
    try:
        slot = TimeSlot.objects.select_for_update().get(
            pk=slot_id, teacher=request.teacher_profile,
        )
    except TimeSlot.DoesNotExist:
        return _json_error('Слот не найден', status=404)

    if request.method == 'DELETE':
        if slot.status in ('held', 'booked'):
            return _json_error(
                'Нельзя удалить слот с активным бронированием. '
                'Сначала отмените booking.', status=409,
            )
        # Слот с историей броней (включая отменённые/завершённые) удалять
        # нельзя: брони несут денежную историю, Booking.slot = PROTECT.
        if slot.bookings.exists():
            return _json_error(
                'Слот связан с историей уроков и не может быть удалён. '
                'Вместо удаления переведите его в «недоступен» (blocked).',
                status=409,
            )
        slot.delete()
        return JsonResponse({'deleted': slot_id})

    # PATCH
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return _json_error('Invalid JSON')

    if slot.status in ('held', 'booked'):
        return _json_error(
            'Нельзя изменить слот с активным бронированием.', status=409,
        )

    start = _parse_iso(data.get('start')) if data.get('start') else slot.start_at
    end = _parse_iso(data.get('end')) if data.get('end') else slot.end_at
    new_status = data.get('status', slot.status)

    if not start or not end or start >= end:
        return _json_error('Неверный временной диапазон')
    if end - start > timezone.timedelta(hours=8):
        return _json_error('Длительность слота не может быть больше 8 часов')
    if new_status not in ('free', 'blocked'):
        return _json_error('status должен быть free или blocked')

    # Проверяем пересечение (исключая сам слот)
    overlap = TimeSlot.objects.filter(
        teacher=request.teacher_profile,
        start_at__lt=end,
        end_at__gt=start,
    ).exclude(pk=slot.pk).exists()
    if overlap:
        return _json_error('Слот пересекается с существующим', status=409)

    slot.start_at = start
    slot.end_at = end
    slot.status = new_status
    try:
        # Savepoint: exclusion-констрейнт (0048) ловит гонку, которую
        # python-проверка выше не закрывает.
        with transaction.atomic():
            slot.save(update_fields=['start_at', 'end_at', 'status', 'updated_at'])
    except IntegrityError:
        return _json_error('Слот пересекается с существующим', status=409)

    return JsonResponse({'event': _slot_to_event(slot)})


# =============================================================================
# Phase 5.5 — bulk-операции: генерация из weekly_schedule, массовое удаление
# =============================================================================

@teacher_required
@require_http_methods(['POST'])
@transaction.atomic
def slots_bulk_generate_api(request):
    """
    Создать TimeSlot пачкой из шаблонного расписания TeacherProfile.weekly_schedule.

    Body:
        weeks (int, default 4) — на сколько недель вперёд
        slot_minutes (int, default 60) — длительность одного слота в минутах
        starting_from (ISO date, optional) — с какой даты начать (default: завтра)

    Логика:
        Для каждого дня недели из weekly_schedule
        В заданном интервале [from, to] нарезаем слоты по slot_minutes
        Пропускаем слоты в прошлом и пересекающиеся с существующими

    Возвращает: {created: N, skipped: M, total_attempted: K}
    """
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return _json_error('Invalid JSON')

    weeks = int(data.get('weeks') or 4)
    if not (1 <= weeks <= 12):
        return _json_error('weeks должно быть от 1 до 12')

    slot_minutes = int(data.get('slot_minutes') or 60)
    if slot_minutes not in (30, 45, 60, 90, 120):
        return _json_error('slot_minutes должно быть 30/45/60/90/120')

    from datetime import datetime, time as dt_time, timedelta
    teacher = request.teacher_profile

    # Если фронт прислал отредактированный шаблон — валидируем, сохраняем в
    # weekly_schedule (чтобы профиль и календарь были в синхроне) и генерируем
    # уже из него. Иначе берём сохранённый ранее шаблон.
    if 'schedule' in data and data.get('schedule') is not None:
        normalized, err = _normalize_schedule_payload(data['schedule'])
        if err:
            return _json_error(err, status=400)
        teacher.weekly_schedule = normalized
        teacher.save(update_fields=['weekly_schedule'])

    # Нормализованное расписание: {day_key: [(from, to), ...]} (оба формата хранения)
    schedule = teacher.get_schedule_intervals()
    if not any(schedule.values()):
        return _json_error(
            'Расписание пустое. Включите хотя бы один рабочий день с интервалом '
            'времени и попробуйте снова.',
            status=400,
        )

    # Маппинг weekday → Python weekday (Mon=0)
    weekday_map = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6,
    }

    tz = timezone.get_current_timezone()
    now = timezone.now()
    starting_from_str = data.get('starting_from')
    if starting_from_str:
        try:
            start_date = datetime.fromisoformat(starting_from_str).date()
        except ValueError:
            return _json_error('starting_from должен быть ISO date (YYYY-MM-DD)')
    else:
        start_date = (now + timedelta(days=1)).date()  # завтра

    end_date = start_date + timedelta(weeks=weeks)

    # Заранее тянем существующие слоты учителя в этом диапазоне для проверки пересечений
    existing = list(TimeSlot.objects.filter(
        teacher=teacher,
        start_at__gte=timezone.make_aware(datetime.combine(start_date, dt_time(0, 0)), tz),
        start_at__lt=timezone.make_aware(datetime.combine(end_date, dt_time(0, 0)), tz),
    ).values_list('start_at', 'end_at'))

    def overlaps_existing(s_start, s_end):
        for ex_start, ex_end in existing:
            if s_start < ex_end and ex_start < s_end:
                return True
        return False

    created = 0
    skipped = 0
    total_attempted = 0

    current = start_date
    while current < end_date:
        weekday_name = current.strftime('%A').lower()  # 'monday', 'tuesday'...
        # Каждый день может содержать НЕСКОЛЬКО интервалов
        for from_str, to_str in schedule.get(weekday_name, []):
            try:
                from_t = dt_time.fromisoformat(from_str)
                to_t = dt_time.fromisoformat(to_str)
            except (ValueError, TypeError):
                continue

            day_start = timezone.make_aware(datetime.combine(current, from_t), tz)
            day_end = timezone.make_aware(datetime.combine(current, to_t), tz)

            cursor = day_start
            while cursor + timedelta(minutes=slot_minutes) <= day_end:
                slot_end = cursor + timedelta(minutes=slot_minutes)
                total_attempted += 1
                # В прошлом → skip
                if cursor < now:
                    skipped += 1
                # Пересекается с существующим → skip
                elif overlaps_existing(cursor, slot_end):
                    skipped += 1
                else:
                    try:
                        # Savepoint: exclusion-констрейнт (0048) ловит гонку
                        # с параллельным созданием — конфликт считаем skip.
                        with transaction.atomic():
                            TimeSlot.objects.create(
                                teacher=teacher,
                                start_at=cursor,
                                end_at=slot_end,
                                status='free',
                            )
                        existing.append((cursor, slot_end))  # для последующих проверок в том же запуске
                        created += 1
                    except IntegrityError:
                        skipped += 1
                cursor = slot_end

        current += timedelta(days=1)

    logger.info(
        f'bulk_generate: teacher={teacher.pk} weeks={weeks} '
        f'created={created} skipped={skipped} total={total_attempted}'
    )
    return JsonResponse({
        'created': created,
        'skipped': skipped,
        'total_attempted': total_attempted,
        'weeks': weeks,
        'slot_minutes': slot_minutes,
    }, status=201 if created else 200)


@teacher_required
@require_http_methods(['POST'])
@transaction.atomic
def slots_bulk_delete_api(request):
    """
    Массово удалить слоты в диапазоне.
    Body: {from: ISO datetime, to: ISO datetime}

    Удаляются ТОЛЬКО свободные слоты без истории броней. Клиентский флаг
    only_free больше не принимается: с {"only_free": false} запрос удалял
    booked-слоты, а CASCADE уносил брони с денежной историей (escrow/payout).
    """
    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return _json_error('Invalid JSON')

    start = _parse_iso(data.get('from'))
    end = _parse_iso(data.get('to'))
    if not start or not end:
        return _json_error('from и to обязательны')
    if start >= end:
        return _json_error('from должен быть раньше to')

    qs = TimeSlot.objects.filter(
        teacher=request.teacher_profile,
        start_at__gte=start,
        start_at__lt=end,
        status='free',
        # Слоты с историей броней (отменённые/завершённые уроки) не трогаем —
        # Booking.slot = PROTECT, их удаление уронило бы весь bulk-запрос.
        bookings__isnull=True,
    )

    count = qs.count()
    qs.delete()

    logger.info(f'bulk_delete: teacher={request.teacher_profile.pk} '
                f'range={start}..{end} deleted={count}')
    return JsonResponse({'deleted': count, 'only_free': True})


# =============================================================================
# Phase 3 — Booking API для учеников и учителей
# =============================================================================

def _public_slot_to_event(slot: TimeSlot) -> dict:
    """Сериализация slot для публичного просмотра (только нужные поля)."""
    return {
        'id': slot.pk,
        'start': slot.start_at.isoformat(),
        'end': slot.end_at.isoformat(),
        'title': 'Свободно',
        'backgroundColor': '#10B981',
        'borderColor': '#059669',
        'textColor': '#FFFFFF',
        'extendedProps': {
            'duration_minutes': slot.duration_minutes,
        },
    }


def _booking_to_dict(b: Booking, has_review: bool | None = None) -> dict:
    """Сериализация бронирования для JSON-ответов.

    has_review: если передан — используется без запроса в БД (для списочных
    эндпоинтов, которые предвычисляют флаг batch'ем — см. my_bookings_api).
    """
    if has_review is None:
        has_review = Review.objects.filter(booking=b).exists()
    # Спор (ТЗ шаг 8): кнопка доступна для завершённого оплаченного урока в
    # пределах grace-окна, если спора ещё нет.
    from datetime import timedelta as _td
    from django.conf import settings as _st
    try:
        _disp = b.dispute
    except Exception:
        _disp = None
    _grace = getattr(_st, 'PAYOUT_GRACE_HOURS', 24)
    _has_money = bool(b.subscription_id) or bool(b.is_trial and b.trial_price_paid)
    _payout_at = b.slot.end_at + _td(hours=_grace)
    _within_grace = _payout_at > timezone.now()
    _can_dispute = bool(
        b.status == 'completed' and _has_money and _disp is None and _within_grace
    )
    # «Деньги под проверкой»: доставленный оплаченный урок, выплата учителю ещё
    # не ушла (идёт grace-окно), спор не открыт. Показываем ученику бейдж.
    _escrow_hold = bool(
        b.status in ('completed', 'no_show_student') and _has_money
        and _disp is None and _within_grace
    )
    return {
        'id': str(b.id),
        'status': b.status,
        'status_display': b.get_status_display(),
        'is_trial': b.is_trial,
        # Подписочный урок переносится сразу в confirmed (без повторного
        # подтверждения учителя) — клиент по этому флагу показывает корректный текст.
        'is_subscription': bool(b.subscription_id),
        'created_at': b.created_at.isoformat(),
        'expires_at': b.expires_at.isoformat() if b.expires_at else None,
        'student_message': b.student_message,
        'teacher_reply': b.teacher_reply,
        'meeting_url': b.meeting_url,
        # Внутренняя комната (встроенный Jitsi) — для авто-комнат; для кастомных
        # внешних ссылок meeting_is_jitsi=False и заходим напрямую по meeting_url.
        'meeting_is_jitsi': b.is_jitsi_meeting(),
        'lesson_room_url': reverse('lesson_room', args=[b.id]),
        # Архив урока (материалы + чат, read-only) — доступен после начала урока,
        # без ограничения по времени. Постоянство материалов после занятия.
        'archive_url': reverse('lesson_archive', args=[b.id]),
        'can_view_archive': timezone.now() >= b.slot.start_at,
        # Отзыв: ученик может оценить завершённый урок (или обновить отзыв)
        'review_url': reverse('leave_review', args=[b.id]),
        'has_review': has_review,
        # Спор по уроку
        'dispute_url': reverse('dispute_open', args=[b.id]),
        'dispute_status': _disp.status if _disp else None,
        'can_dispute': _can_dispute,
        # «Деньги под проверкой» (escrow/grace) — для бейджа в UI.
        'escrow_hold': _escrow_hold,
        'payout_at': _payout_at.isoformat() if _escrow_hold else None,
        'subject': {
            'id': b.subject.pk,
            'name': b.subject.name,
        } if b.subject else None,
        'slot': {
            'id': b.slot.pk,
            'start': b.slot.start_at.isoformat(),
            'end': b.slot.end_at.isoformat(),
            'duration_minutes': b.slot.duration_minutes,
        },
        'teacher': {
            'id': b.slot.teacher.pk,
            'name': b.slot.teacher.user.get_full_name() or b.slot.teacher.user.username,
            'username': b.slot.teacher.user.username,
        },
        'student': {
            'id': b.student.pk,
            'name': b.student.get_full_name() or b.student.username,
            'username': b.student.username,
        },
    }


def _can_view_booking(user, booking: Booking) -> bool:
    """Студент видит свою бронь, учитель — на свои slots."""
    if booking.student_id == user.pk:
        return True
    teacher_profile = getattr(user, 'teacher_profile', None)
    if teacher_profile and booking.slot.teacher_id == teacher_profile.pk:
        return True
    return user.is_staff


# ---------------------------------------------------------------- PUBLIC ---

@require_http_methods(['GET'])
def public_teacher_slots(request, teacher_id: int):
    """
    Публичный список СВОБОДНЫХ слотов учителя в указанном диапазоне.
    Используется на странице профиля учителя в мини-календаре.
    Не требует авторизации (гости тоже могут посмотреть).
    """
    try:
        teacher = TeacherProfile.objects.get(pk=teacher_id, moderation_status='approved')
    except TeacherProfile.DoesNotExist:
        return _json_error('Учитель не найден', status=404)

    start = _parse_iso(request.GET.get('start'))
    end = _parse_iso(request.GET.get('end'))
    if not start or not end:
        return _json_error('start и end (ISO datetime) обязательны')

    # Не показываем уже прошедшие слоты — никакого смысла
    now = timezone.now()
    if start < now:
        start = now

    slots = TimeSlot.objects.filter(
        teacher=teacher,
        status='free',
        start_at__gte=start,
        start_at__lt=end,
    ).order_by('start_at')

    return JsonResponse({'events': [_public_slot_to_event(s) for s in slots]})


# ---------------------------------------------------------------- STUDENT --

@student_required
@require_http_methods(['POST'])
def booking_create_api(request):
    """
    Ученик создаёт booking на конкретный slot.
    Body: {slot_id, subject_id?, message?, is_trial?}
    Атомарно через Booking.create_hold (select_for_update + UniqueConstraint).
    Rate-limited: 20 заявок/час на пользователя — анти-спам.
    """
    from django_ratelimit.core import is_ratelimited
    if is_ratelimited(request=request, group='booking_create', key='user',
                       rate='20/h', method='POST', increment=True):
        return _json_error('Слишком много заявок. Подождите час и попробуйте снова.', status=429)

    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return _json_error('Invalid JSON')

    slot_id = data.get('slot_id')
    if not slot_id:
        return _json_error('slot_id обязателен')

    subject_id = data.get('subject_id')
    subject = None
    if subject_id:
        try:
            subject = Subject.objects.get(pk=subject_id, is_active=True)
        except Subject.DoesNotExist:
            return _json_error('Предмет не найден', status=400)

    message = (data.get('message') or '').strip()
    # Анти-обход платформы: первое сообщение учителю — такой же канал обмена
    # контактами, как чат. Маскируем по тому же порогу доверия (до N оплаченных
    # уроков в паре). Один доп. запрос только при непустом сообщении.
    if message:
        from .contact_filter import mask_for_pair
        _slot_for_mask = TimeSlot.objects.select_related('teacher').filter(pk=slot_id).first()
        if _slot_for_mask is not None:
            message, _masked = mask_for_pair(request.user, _slot_for_mask.teacher, message)
    is_trial = bool(data.get('is_trial', False))

    # Phase 9.5: для пробного выясняем — платный или бесплатный.
    # Платный → проходит через TrialService (debit + escrow + idempotency).
    # Бесплатный → обычный create_hold (без денег).
    teacher_subject = None
    if is_trial:
        try:
            slot_obj = TimeSlot.objects.get(pk=slot_id)
        except TimeSlot.DoesNotExist:
            return _json_error('Слот не найден', status=404)

        if subject is None:
            return _json_error('subject_id обязателен для пробного урока', status=400)

        from .models import TeacherSubject
        teacher_subject = TeacherSubject.objects.filter(
            teacher=slot_obj.teacher, subject=subject,
        ).first()
        if teacher_subject is None:
            return _json_error('Учитель не преподаёт этот предмет', status=400)

    # Политику бронирования решает backend (единственный источник истины):
    #   • бесплатный пробный — один на всю платформу (глобально по ученику);
    #   • после него любая разовая бронь требует депозита (это и есть оплата урока);
    #   • платный пробный — отдельный продукт учителя (анти-абуз per-teacher).
    from billing.deposits import DepositService, has_used_free_trial

    try:
        if is_trial and teacher_subject and not teacher_subject.is_free_trial \
                and teacher_subject.trial_price:
            # Платный пробный — через TrialService (с debit'ом). Анти-абуз
            # (один платный пробный на пару) — внутри сервиса.
            from billing.services import (
                InsufficientFunds, TrialAlreadyTaken, TrialNotPaid, TrialService,
            )
            try:
                booking = TrialService.book_paid_trial(
                    student=request.user,
                    slot_id=slot_id,
                    teacher_subject=teacher_subject,
                    message=message,
                )
            except InsufficientFunds as e:
                from urllib.parse import urlencode
                topup_url = reverse('wallet_topup_request') + '?' + urlencode({
                    'amount': int(teacher_subject.trial_price),
                    'next': reverse('book_teacher_page',
                                    kwargs={'teacher_id': slot_obj.teacher_id}),
                })
                return _json_error(
                    f'Недостаточно средств на балансе для оплаты пробного урока '
                    f'({int(teacher_subject.trial_price)} сум). Пополните кошелёк.',
                    status=402,
                    code='insufficient_funds',
                    topup_url=topup_url,
                )
            except TrialAlreadyTaken as e:
                return _json_error(str(e), status=409)
            except TrialNotPaid as e:
                # Не должно случиться — branch гарантирует, но защита.
                return _json_error(str(e), status=400)
        elif is_trial and has_used_free_trial(request.user):
            # Явно запросили бесплатный пробный, но он уже израсходован. Не
            # списываем депозит молча. Разовый урок оплачивается депозитом с
            # баланса — если баланса не хватает, отдаём topup_url (страница
            # оплаты), и фронт показывает кнопку «Пополнить баланс».
            from billing.deposits import get_deposit_amount
            from billing.services import WalletService
            from urllib.parse import urlencode
            dep = get_deposit_amount()
            wallet = WalletService.get_or_create_wallet(request.user)
            _slot = TimeSlot.objects.select_related('teacher').filter(pk=slot_id).first()
            _next = (reverse('book_teacher_page', kwargs={'teacher_id': _slot.teacher_id})
                     if _slot else '')
            _params = {'amount': int(dep)}
            if _next:
                _params['next'] = _next
            topup_url = reverse('wallet_topup_request') + '?' + urlencode(_params)
            if wallet.balance < dep:
                return _json_error(
                    f'Бесплатный пробный урок уже использован. Разовый урок '
                    f'оплачивается депозитом {int(dep)} сум — пополните баланс.',
                    status=409, code='free_trial_used', topup_url=topup_url,
                )
            return _json_error(
                f'Бесплатный пробный урок уже использован. Снимите отметку '
                f'«пробный» и забронируйте разовый урок — спишется депозит {int(dep)} сум.',
                status=409, code='free_trial_used',
            )
        elif not has_used_free_trial(request.user):
            # Первая бронь ученика — это его единственный бесплатный пробный
            # (независимо от того, помечена ли она is_trial фронтом). Денег нет.
            booking = Booking.create_hold(
                slot_id=slot_id,
                student=request.user,
                subject=subject,
                message=message,
                is_trial=True,
            )
        else:
            # Пробный уже израсходован → разовая бронь требует депозита. Депозит —
            # не доп. платёж, а оплата урока; удерживается сразу при бронировании.
            from billing.services import InsufficientFunds
            try:
                booking = DepositService.book_with_deposit(
                    student=request.user,
                    slot_id=slot_id,
                    subject=subject,
                    message=message,
                )
            except InsufficientFunds:
                from urllib.parse import urlencode
                from billing.deposits import get_deposit_amount
                _slot = TimeSlot.objects.select_related('teacher').filter(pk=slot_id).first()
                deposit_amount = int(get_deposit_amount())
                params = {'amount': deposit_amount}
                if _slot is not None:
                    params['next'] = reverse('book_teacher_page',
                                             kwargs={'teacher_id': _slot.teacher_id})
                topup_url = reverse('wallet_topup_request') + '?' + urlencode(params)
                return _json_error(
                    f'Недостаточно средств для депозита ({deposit_amount} сум). '
                    f'Пополните кошелёк.',
                    status=402,
                    code='insufficient_funds',
                    topup_url=topup_url,
                )
    except SlotUnavailable as e:
        return _json_error(f'Слот уже занят или прошёл: {e}', status=409)
    except TimeSlot.DoesNotExist:
        return _json_error('Слот не найден', status=404)
    except Exception as e:
        logger.error(f'booking_create_api failed: {e}', exc_info=True)
        return _json_error('Не удалось создать бронирование', status=500)

    # Notify teacher (in-app + WS)
    _notify_teacher_about_booking(booking)

    logger.info(f'Booking created: {booking.pk} student={request.user.pk} slot={slot_id}')
    return JsonResponse({'booking': _booking_to_dict(booking)}, status=201)


@student_required
@require_http_methods(['GET'])
def booking_eligibility_api(request):
    """Состояние бронирования для ученика — фронт лишь отображает это.

    Backend — единственный источник истины (enforcement в booking_create_api
    использует ту же политику). Возвращает: доступен ли бесплатный пробный,
    нужен ли депозит и его сумму, а также хватает ли баланса на депозит.
    """
    from billing.deposits import BookingPolicyService
    from billing.services import WalletService

    elig = BookingPolicyService.evaluate(request.user)
    wallet = WalletService.get_or_create_wallet(request.user)
    payload = elig.as_dict()
    payload['wallet_balance'] = str(wallet.balance)
    payload['sufficient_balance'] = wallet.balance >= elig.deposit_amount
    return JsonResponse(payload)


@authenticated_required
@require_http_methods(['POST'])
def booking_cancel_api(request, booking_id):
    """
    Студент или учитель отменяет booking.
    - Студент → cancelled_by_student
    - Учитель (владелец slot) → cancelled_by_teacher
    """
    try:
        booking = Booking.objects.select_related('slot__teacher__user', 'student').get(pk=booking_id)
    except Booking.DoesNotExist:
        return _json_error('Бронирование не найдено', status=404)

    if not _can_view_booking(request.user, booking):
        return _json_error('Доступ запрещён', status=403)

    if booking.status not in ('pending', 'confirmed'):
        return _json_error(f'Нельзя отменить бронирование в статусе {booking.get_status_display()}', status=409)

    try:
        if request.user.pk == booking.student_id:
            booking.cancel_by_student()
            _notify_teacher_about_cancellation(booking, by='student')
        elif getattr(request.user, 'teacher_profile', None) and request.user.teacher_profile.pk == booking.slot.teacher_id:
            # Учитель отменил confirmed (или pending) — фактически reject-семантика для pending
            if booking.status == 'pending':
                _reply = (json.loads(request.body or '{}').get('reply', '') or '').strip()
                if _reply:
                    from .contact_filter import mask_for_pair
                    _reply, _masked = mask_for_pair(booking.student, booking.slot.teacher, _reply)
                booking.reject(teacher_reply=_reply)
            else:
                # confirmed → cancelled_by_teacher (race-safe, под select_for_update)
                booking.cancel_by_teacher()
            _notify_student_about_decision(booking, decision='cancelled')
        else:
            return _json_error('Доступ запрещён', status=403)
    except ValueError as e:
        return _json_error(str(e), status=409)

    # Phase 9.5: refund платного пробного при отмене любой стороной.
    if booking.is_trial and booking.trial_price_paid:
        try:
            from billing.services import TrialService
            refunded = TrialService.refund_trial(booking, reason='Отмена бронирования')
            if refunded > 0:
                logger.info(f'Trial refund: booking={booking.id}, amount={refunded}')
        except Exception as e:
            logger.error(f'Trial refund failed for booking={booking.id}: {e}', exc_info=True)
    # Урок подписки: применяем политику отмены (v2 Шаг 5). Заблаговременная
    # отмена → возврат в квоту; поздняя отмена ученика → урок списывается
    # учителю. Иначе деньги зависали бы в эскроу.
    elif booking.subscription_id:
        try:
            from billing.services import SubscriptionService
            by = 'student' if request.user.pk == booking.student_id else 'teacher'
            result = SubscriptionService.cancel_lesson(
                booking, cancelled_by=by, reason='Отмена урока',
            )
            logger.info(
                f'Lesson cancel: booking={booking.id}, policy={result["policy"]}, '
                f'refunded={result["refunded"]}, charged={result["charged"]}'
            )
        except Exception as e:
            logger.error(f'Lesson cancel failed for booking={booking.id}: {e}', exc_info=True)
    else:
        # Разовый урок с депозитом: отмена до урока → депозит возвращается
        # (сгорает только при неявке). refund — no-op, если депозита нет.
        try:
            from billing.deposits import DepositService
            refunded = DepositService.refund(booking, reason='Отмена бронирования')
            if refunded > 0:
                logger.info(f'Deposit refund: booking={booking.id}, amount={refunded}')
        except Exception as e:
            logger.error(f'Deposit refund failed for booking={booking.id}: {e}', exc_info=True)

    return JsonResponse({'booking': _booking_to_dict(booking)})


@authenticated_required
@require_http_methods(['POST'])
def booking_report_teacher_noshow_api(request, booking_id):
    """Ученик сообщает, что преподаватель не подключился к начавшемуся уроку.

    Закрывает тупик: вместо ожидания Celery (end_at+30) ученик сразу получает
    возврат — но ТОЛЬКО при объективном отсутствии преподавателя в нашей
    видеокомнате (см. Booking.student_report_teacher_no_show). Иначе 409 с
    предложением открыть спор.
    """
    try:
        booking = Booking.objects.select_related('slot__teacher__user', 'student').get(pk=booking_id)
    except Booking.DoesNotExist:
        return _json_error('Бронирование не найдено', status=404)
    if booking.student_id != request.user.pk:
        return _json_error('Доступ запрещён', status=403)
    try:
        booking.student_report_teacher_no_show()
    except ValueError as e:
        return _json_error(str(e), status=409)
    # Возврат ученику + уведомление — тот же путь, что Celery-поток settle.
    from .tasks import _refund_teacher_no_show, _notify_teacher_no_show
    _refund_teacher_no_show(booking)
    _notify_teacher_no_show(booking)
    logger.info(f'Teacher no-show reported by student: booking={booking.id}')
    return JsonResponse({
        'ok': True,
        'status': booking.status,
        'message': 'Урок отмечен как неявка преподавателя. Средства возвращены — выберите новую дату.',
    })


@student_required
@require_http_methods(['POST'])
def booking_reschedule_api(request, booking_id):
    """
    Ученик переносит свою активную бронь на другой свободный слот того же учителя.
    Body: {slot_id}. После переноса бронь снова pending — учитель подтверждает.
    """
    try:
        booking = Booking.objects.select_related('slot__teacher__user', 'student').get(pk=booking_id)
    except Booking.DoesNotExist:
        return _json_error('Бронирование не найдено', status=404)

    if booking.student_id != request.user.pk:
        return _json_error('Доступ запрещён', status=403)

    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return _json_error('Invalid JSON')

    new_slot_id = data.get('slot_id')
    if not new_slot_id:
        return _json_error('slot_id обязателен')

    try:
        new_status = booking.reschedule_by_student(new_slot_id)
    except SlotUnavailable as e:
        return _json_error(f'Слот недоступен: {e}', status=409)
    except TimeSlot.DoesNotExist:
        return _json_error('Слот не найден', status=404)
    except ValueError as e:
        return _json_error(str(e), status=409)

    # Подписочный урок переносится сразу в confirmed (повторное подтверждение не
    # нужно) — просто уведомляем учителя о смене времени. Разовый/пробный урок
    # возвращается в pending → учитель должен подтвердить новое время.
    _notify_teacher_about_booking(booking)
    logger.info(f'Booking rescheduled: {booking.pk} → slot={new_slot_id} (status={new_status})')
    return JsonResponse({'booking': _booking_to_dict(booking)})


# ---------------------------------------------------------------- TEACHER --

_MEETING_URL_MAX_LEN = 500
_MEETING_URL_ALLOWED_SCHEMES = ('https://',)


def _validate_meeting_url(url: str) -> tuple[bool, str]:
    """
    Валидация URL для встречи: должен быть http(s), длина ≤ 500, выглядит как URL.

    Анти-обход (v2 Шаг 7): если ALLOW_EXTERNAL_MEETING_URLS выключено (по умолчанию),
    внешние ссылки (Zoom/Meet и т.п.) запрещены — разрешена только встроенная
    Jitsi-комната. Это закрывает канал увода урока с платформы и сохраняет
    корректный детект неявок (присутствие трекается только в нашей комнате).

    Возвращает (ok, error_msg).
    """
    from django.conf import settings
    url = (url or '').strip()
    if not url:
        return True, ''  # пусто — это ок, подставится наша Jitsi-комната
    if len(url) > _MEETING_URL_MAX_LEN:
        return False, f'URL слишком длинный (макс {_MEETING_URL_MAX_LEN})'
    if not url.lower().startswith(_MEETING_URL_ALLOWED_SCHEMES):
        return False, 'Ссылка должна начинаться с https://'
    # Минимальная sanity-проверка: должен быть хост
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        if not parsed.netloc or '.' not in parsed.netloc:
            return False, 'Некорректный URL'
    except Exception:
        return False, 'Некорректный URL'
    # Запрет внешних ссылок (кроме нашей Jitsi-комнаты).
    if not getattr(settings, 'ALLOW_EXTERNAL_MEETING_URLS', False):
        base = (getattr(settings, 'JITSI_BASE_URL', '') or '').rstrip('/')
        if not (base and url.startswith(base)):
            return False, (
                'Внешние ссылки на встречу отключены. Оставьте поле пустым — '
                'урок пройдёт во встроенной видеокомнате платформы.'
            )
    return True, ''


@teacher_required
@require_http_methods(['POST'])
def booking_confirm_api(request, booking_id):
    """
    Учитель подтверждает pending booking → confirmed, slot → booked.
    Body: { reply?, meeting_url? }  — meeting_url опционально.
    Rate-limited: 60/час на учителя (защита от случайных двойных кликов и спама).
    """
    from django_ratelimit.core import is_ratelimited
    if is_ratelimited(request=request, group='booking_confirm', key='user',
                       rate='60/h', method='POST', increment=True):
        return _json_error('Слишком много запросов. Подождите.', status=429)

    try:
        booking = Booking.objects.select_related('slot__teacher__user', 'student').get(pk=booking_id)
    except Booking.DoesNotExist:
        return _json_error('Бронирование не найдено', status=404)

    if booking.slot.teacher_id != request.teacher_profile.pk:
        return _json_error('Доступ запрещён', status=403)

    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        data = {}

    reply = (data.get('reply') or '').strip()
    if reply:
        from .contact_filter import mask_for_pair
        reply, _masked = mask_for_pair(booking.student, booking.slot.teacher, reply)
    meeting_url = (data.get('meeting_url') or '').strip()

    ok, err = _validate_meeting_url(meeting_url)
    if not ok:
        return _json_error(f'meeting_url: {err}', status=400)

    try:
        booking.confirm(teacher_reply=reply)
    except ValueError as e:
        return _json_error(str(e), status=409)

    if meeting_url:
        booking.meeting_url = meeting_url
        booking.save(update_fields=['meeting_url', 'updated_at'])

    _notify_student_about_decision(booking, decision='confirmed')
    logger.info(f'Booking confirmed: {booking.pk}, meeting_url={"custom" if meeting_url else "auto-jitsi"}')
    return JsonResponse({'booking': _booking_to_dict(booking)})


@teacher_required
@require_http_methods(['POST', 'PATCH'])
def booking_set_meeting_url_api(request, booking_id):
    """
    Обновить meeting_url у уже подтверждённого booking.
    Учитель может задать/изменить ссылку и после confirm (например, если забыл).
    """
    try:
        booking = Booking.objects.select_related('slot__teacher__user').get(pk=booking_id)
    except Booking.DoesNotExist:
        return _json_error('Бронирование не найдено', status=404)

    if booking.slot.teacher_id != request.teacher_profile.pk:
        return _json_error('Доступ запрещён', status=403)
    if booking.status not in ('pending', 'confirmed'):
        return _json_error('Можно установить ссылку только для активного бронирования', status=409)

    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return _json_error('Invalid JSON')

    url = (data.get('meeting_url') or '').strip()
    ok, err = _validate_meeting_url(url)
    if not ok:
        return _json_error(err, status=400)

    # Пустую ссылку трактуем как «вернуть нашу Jitsi-комнату», а НЕ «убрать
    # комнату». Иначе учитель мог обнулить meeting_url → is_jitsi_meeting()==False
    # → settle_after_end никогда не пометит no-show → выплата без присутствия.
    if not url:
        url = booking.build_meeting_url()

    booking.meeting_url = url
    booking.save(update_fields=['meeting_url', 'updated_at'])
    return JsonResponse({'booking': _booking_to_dict(booking)})


@teacher_required
@require_http_methods(['POST'])
def booking_reject_api(request, booking_id):
    """Учитель отклоняет pending booking → cancelled_by_teacher, slot → free."""
    try:
        booking = Booking.objects.select_related('slot__teacher__user', 'student').get(pk=booking_id)
    except Booking.DoesNotExist:
        return _json_error('Бронирование не найдено', status=404)

    if booking.slot.teacher_id != request.teacher_profile.pk:
        return _json_error('Доступ запрещён', status=403)

    try:
        data = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        data = {}
    reply = (data.get('reply') or '').strip()
    if reply:
        from .contact_filter import mask_for_pair
        reply, _masked = mask_for_pair(booking.student, booking.slot.teacher, reply)

    try:
        booking.reject(teacher_reply=reply)
    except ValueError as e:
        return _json_error(str(e), status=409)

    # Phase 9.5: refund платного пробного, если учитель reject'нул.
    if booking.is_trial and booking.trial_price_paid:
        try:
            from billing.services import TrialService
            TrialService.refund_trial(booking, reason=f'Отказ учителя: {reply[:100]}')
        except Exception as e:
            logger.error(f'Trial refund failed for booking={booking.id}: {e}', exc_info=True)
    else:
        # Разовый урок с депозитом: учитель отклонил → депозит возвращается.
        try:
            from billing.deposits import DepositService
            DepositService.refund(booking, reason=f'Отказ учителя: {reply[:100]}')
        except Exception as e:
            logger.error(f'Deposit refund failed for booking={booking.id}: {e}', exc_info=True)

    _notify_student_about_decision(booking, decision='rejected')
    logger.info(f'Booking rejected: {booking.pk}')
    return JsonResponse({'booking': _booking_to_dict(booking)})


# ---------------------------------------------------------------- LIST -----

@authenticated_required
@require_http_methods(['GET'])
def my_bookings_api(request):
    """
    Список моих бронирований.
    Студент видит свои, учитель — те что на его slots.
    Query params: status (optional), upcoming=true (default: всё)
    """
    status_filter = request.GET.get('status')
    upcoming = request.GET.get('upcoming', '').lower() in ('1', 'true', 'yes')

    qs = Booking.objects.select_related(
        'slot__teacher__user', 'student', 'subject', 'dispute'
    )
    if request.user.user_type == 'student':
        qs = qs.filter(student=request.user)
    elif request.user.user_type == 'teacher':
        try:
            tp = request.user.teacher_profile
            qs = qs.filter(slot__teacher=tp)
        except TeacherProfile.DoesNotExist:
            return JsonResponse({'bookings': []})

    if status_filter:
        qs = qs.filter(status=status_filter)
    if upcoming:
        qs = qs.filter(slot__end_at__gte=timezone.now())

    qs = qs.order_by('slot__start_at')[:200]
    bookings = list(qs)
    # Предвычисляем has_review одним запросом, чтобы не делать exists() в цикле (N+1)
    reviewed_ids = set(
        Review.objects.filter(booking_id__in=[b.id for b in bookings])
        .values_list('booking_id', flat=True)
    )
    return JsonResponse({
        'bookings': [_booking_to_dict(b, has_review=b.id in reviewed_ids) for b in bookings]
    })


@authenticated_required
def my_bookings_page(request):
    """Страница 'Мои бронирования / Уроки'."""
    return render(request, 'booking/my_bookings.html', {
        'role': request.user.user_type,
        # Окно активности кнопки «Войти в урок» — должно совпадать с серверным
        # окном в lesson_room/lesson_attendance_api (−lead … +grace).
        'join_lead_minutes': getattr(settings, 'LESSON_JOIN_LEAD_MINUTES', 10),
        'join_grace_minutes': getattr(settings, 'LESSON_JOIN_GRACE_MINUTES', 30),
        # Порог, после которого ученик может отметить неявку преподавателя —
        # должен совпадать с серверной проверкой (student_report_teacher_no_show).
        'no_show_report_minutes': getattr(settings, 'TEACHER_NO_SHOW_REPORT_AFTER_MINUTES', 15),
        # Минимальный лид-тайм переноса — чтобы кнопку «Перенести» гасить заранее,
        # а не показывать ошибку после выбора слота (совпадает с reschedule_by_student).
        'reschedule_min_lead_hours': getattr(settings, 'RESCHEDULE_MIN_LEAD_HOURS', 4),
    })


def _ical_escape(text: str) -> str:
    """Экранирование текста для iCalendar (RFC 5545)."""
    return (str(text or '')
            .replace('\\', '\\\\').replace(';', '\\;')
            .replace(',', '\\,').replace('\n', '\\n'))


@authenticated_required
@require_http_methods(['GET'])
def booking_ical(request, booking_id):
    """Экспорт брони в файл .ics (Google Calendar / Apple / Outlook)."""
    from django.http import HttpResponse

    booking = get_object_or_404(
        Booking.objects.select_related('slot__teacher__user', 'student', 'subject'),
        pk=booking_id,
    )
    if not _can_view_booking(request.user, booking):
        return HttpResponseForbidden('Доступ запрещён')

    teacher_name = booking.slot.teacher.user.get_full_name() or booking.slot.teacher.user.username
    student_name = booking.student.get_full_name() or booking.student.username
    subject = booking.subject.name if booking.subject else 'Урок'
    summary = f'{subject}: {teacher_name} ↔ {student_name}'

    desc_lines = [f'Учитель: {teacher_name}', f'Ученик: {student_name}']
    if booking.meeting_url:
        desc_lines.append(f'Ссылка на урок: {booking.meeting_url}')
    description = '\\n'.join(_ical_escape(x) for x in desc_lines)

    from datetime import timezone as _dt_tz

    def fmt(dt):
        return dt.astimezone(_dt_tz.utc).strftime('%Y%m%dT%H%M%SZ')

    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//UstozHub//Booking//RU',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'BEGIN:VEVENT',
        f'UID:booking-{booking.id}@ustozhub',
        f'DTSTAMP:{fmt(timezone.now())}',
        f'DTSTART:{fmt(booking.slot.start_at)}',
        f'DTEND:{fmt(booking.slot.end_at)}',
        f'SUMMARY:{_ical_escape(summary)}',
        f'DESCRIPTION:{description}',
    ]
    if booking.meeting_url:
        lines.append(f'URL:{_ical_escape(booking.meeting_url)}')
    lines += [
        'BEGIN:VALARM',
        'TRIGGER:-PT30M',
        'ACTION:DISPLAY',
        f'DESCRIPTION:{_ical_escape(summary)}',
        'END:VALARM',
        'END:VEVENT',
        'END:VCALENDAR',
    ]
    content = '\r\n'.join(lines) + '\r\n'
    resp = HttpResponse(content, content_type='text/calendar; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="lesson-{booking.id}.ics"'
    return resp


def book_teacher_page(request, teacher_id: int):
    """
    Публичная страница 'Забронировать урок' с мини-календарём свободных слотов.
    Гостям показываем, при попытке бронирования — редирект на login.
    """
    try:
        teacher = TeacherProfile.objects.select_related('user', 'city').get(
            pk=teacher_id, moderation_status='approved', is_active=True,
        )
    except TeacherProfile.DoesNotExist:
        messages.error(request, _('Учитель не найден'))
        return redirect('home')

    # Подгружаем предметы учителя для select (включая trial_price и trial_duration)
    teacher_subjects = list(
        teacher.teachersubject_set.select_related('subject')
        .values(
            'subject__id', 'subject__name', 'hourly_rate',
            'is_free_trial', 'trial_price', 'trial_duration_minutes',
        )
    )

    # Баланс кошелька ученика — чтобы показать нехватку до клика по платному пробному.
    wallet_balance = None
    if request.user.is_authenticated and request.user.user_type == 'student':
        try:
            wallet_balance = int(request.user.wallet.balance)
        except Exception:
            wallet_balance = 0

    return render(request, 'booking/book_teacher.html', {
        'teacher': teacher,
        'teacher_subjects': teacher_subjects,
        'wallet_balance': wallet_balance,
    })


@authenticated_required
def lesson_room(request, booking_id):
    """
    Встроенная видео-комната урока (Jitsi через iframe), внутри платформы.

    Доступ только у учителя и ученика этой брони. Открывается за
    LESSON_JOIN_LEAD_MINUTES до начала и до +30 минут после конца. Кастомные
    внешние ссылки (Zoom и т.п.)
    встроить нельзя — для них просто редиректим на ссылку.
    """
    from datetime import timedelta

    booking = get_object_or_404(
        Booking.objects.select_related('slot__teacher__user', 'student', 'subject'),
        pk=booking_id,
    )
    user = request.user
    tp = getattr(user, 'teacher_profile', None)
    is_teacher = bool(tp and booking.slot.teacher_id == tp.pk)
    is_student = (booking.student_id == user.pk)
    if not (is_teacher or is_student):
        return HttpResponseForbidden('Эта комната доступна только участникам урока.')

    # Кастомная внешняя ссылка (не наш Jitsi) — встроить нельзя, ведём напрямую.
    if booking.meeting_url and not booking.is_jitsi_meeting():
        return redirect(booking.meeting_url)

    now = timezone.now()
    lead = getattr(settings, 'LESSON_JOIN_LEAD_MINUTES', 10)
    # Окно входа — из единого источника (Booking.join_opens_at/closes_at), чтобы
    # кнопки «Войти» на дашбордах и реальный доступ к комнате не расходились.
    open_from = booking.join_opens_at
    open_until = booking.join_closes_at

    state = 'ok'
    if booking.status != 'confirmed':
        state = 'not_confirmed'
    elif now < open_from:
        state = 'too_early'
    elif now > open_until:
        state = 'too_late'

    # Присутствие НЕ фиксируем на открытии страницы — только по реальному
    # подключению к видео-конференции (событие Jitsi → lesson_attendance_api).

    jitsi_base = (getattr(settings, 'JITSI_BASE_URL', 'https://meet.jit.si') or '').rstrip('/')
    # Домен-хост для External API (без схемы)
    jitsi_domain = jitsi_base.split('://', 1)[-1]

    other = booking.slot.teacher.user if is_student else booking.student
    other_name = other.get_full_name() or other.username

    # Ученик может отметить неявку преподавателя, если тот объективно не
    # подключился к нашей видеокомнате и прошёл порог опоздания.
    noshow_grace = getattr(settings, 'TEACHER_NO_SHOW_REPORT_AFTER_MINUTES', 15)
    # Право на репорт, НЕ зависящее от времени и присутствия: их учитывает
    # клиентский таймер + индикатор присутствия, чтобы кнопка появлялась без
    # перезагрузки страницы ровно в момент start+grace (аудит §4/§2 P1). Раньше
    # условие вычислялось только на сервере при рендере, и вошедший вовремя
    # ученик кнопку не видел никогда без ручного reload.
    noshow_eligible = (
        is_student
        and booking.status == 'confirmed'
        and not (booking.meeting_url and not booking.is_jitsi_meeting())  # не внешняя ссылка
    )
    # Абсолютный момент, с которого репорт допустим (для клиентского таймера).
    noshow_report_at = booking.slot.start_at + timedelta(minutes=noshow_grace)
    # Серверная подсказка: подключался ли учитель к моменту рендера (клиент
    # дополнительно отслеживает присутствие в реальном времени).
    teacher_present_initial = booking.teacher_joined_at is not None

    return render(request, 'booking/lesson_room.html', {
        'booking': booking,
        'state': state,
        'can_join': state == 'ok',
        'is_teacher': is_teacher,
        'jitsi_domain': jitsi_domain,
        'jitsi_room': booking.jitsi_room_name(),
        'display_name': user.get_full_name() or user.username,
        'other_name': other_name,
        'open_from': open_from,
        'join_lead': lead,
        'noshow_eligible': noshow_eligible,
        'noshow_report_at': noshow_report_at,
        'teacher_present_initial': teacher_present_initial,
        'noshow_grace': noshow_grace,
        'lesson_file_max_mb': settings.LESSON_FILE_MAX_SIZE_MB,
    })


@authenticated_required
@require_http_methods(['GET'])
def lesson_archive(request, booking_id):
    """Архив урока (read-only): материалы + история чата, доступны после урока.

    В отличие от комнаты, НЕ ограничен окном времени — обе стороны могут
    вернуться к переписке и файлам урока когда угодно. Постоянство материалов —
    ключевое преимущество платформы перед разовыми звонками.
    """
    from .models import LessonFile, LessonChatMessage

    booking = get_object_or_404(
        Booking.objects.select_related('slot__teacher__user', 'student', 'subject'),
        pk=booking_id,
    )
    if not _can_view_booking(request.user, booking):
        return HttpResponseForbidden('Этот урок доступен только его участникам.')

    is_teacher = bool(
        getattr(request.user, 'teacher_profile', None)
        and booking.slot.teacher_id == request.user.teacher_profile.pk
    )
    other = booking.slot.teacher.user if not is_teacher else booking.student
    other_name = other.get_full_name() or other.username

    def _icon(name):
        ext = (name.rsplit('.', 1)[-1] if '.' in name else '').lower()
        if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
            return 'fa-file-image'
        return {
            'pdf': 'fa-file-pdf', 'doc': 'fa-file-word', 'docx': 'fa-file-word',
            'ppt': 'fa-file-powerpoint', 'pptx': 'fa-file-powerpoint',
            'xls': 'fa-file-excel', 'xlsx': 'fa-file-excel', 'zip': 'fa-file-zipper',
        }.get(ext, 'fa-file')

    files = [
        {'name': f.file_name, 'url': f.file_url, 'size': f.size, 'icon': _icon(f.file_name)}
        for f in LessonFile.objects.filter(booking=booking).select_related('uploaded_by')
    ]
    chat = []
    for m in (LessonChatMessage.objects.filter(booking=booking)
              .select_related('sender', 'attachment').order_by('created_at')):
        att = None
        if m.attachment_id and m.attachment:
            att = {'name': m.attachment.file_name, 'url': m.attachment.file_url,
                   'size': m.attachment.size, 'icon': _icon(m.attachment.file_name)}
        chat.append({
            'is_mine': m.sender_id == request.user.pk,
            'sender_name': (m.sender.get_full_name() or m.sender.username) if m.sender_id else '',
            'content': m.content,
            'attachment': att,
            'created_at': m.created_at,
        })

    return render(request, 'booking/lesson_archive.html', {
        'booking': booking,
        'other_name': other_name,
        'files': files,
        'chat_messages': chat,
    })


@authenticated_required
@require_http_methods(['POST'])
def lesson_attendance_api(request, booking_id):
    """Приём событий реального присутствия в видео-уроке (Jitsi iframe API).

    Вызывается через navigator.sendBeacon — без постоянного опроса, только на
    вход/выход из конференции (≈2 запроса на участника за урок).
    Body (form): event='join'|'leave', seconds=<int> (для leave).

    'join'  → открываем интервал присутствия стороны (LessonAttendanceSession).
    'leave' → закрываем интервал; по сумме интервалов и overlap settle_after_end
              решает исход урока (completed / no_show / not_held).
    """
    booking = get_object_or_404(
        Booking.objects.select_related('slot__teacher'), pk=booking_id,
    )
    user = request.user
    tp = getattr(user, 'teacher_profile', None)
    is_teacher = bool(tp and booking.slot.teacher_id == tp.pk)
    is_student = (booking.student_id == user.pk)
    if not (is_teacher or is_student):
        return _json_error('Доступ запрещён', status=403)

    # Присутствие засчитываем только для подтверждённого урока и только в окне
    # реального занятия. Окно ДОЛЖНО совпадать с окном входа в lesson_room
    # (−LESSON_JOIN_LEAD_MINUTES … +30 мин): иначе при lead>15 сторона входит в
    # комнату, но beacon join отклоняется → join_at не пишется → settle считает
    # реально присутствовавшего за no-show и ошибочно трогает деньги.
    from datetime import timedelta
    now = timezone.now()
    lead = getattr(settings, 'LESSON_JOIN_LEAD_MINUTES', 10)
    grace = getattr(settings, 'LESSON_JOIN_GRACE_MINUTES', 30)
    if booking.status != 'confirmed':
        return _json_error('Урок не подтверждён', status=409)
    if not (booking.slot.start_at - timedelta(minutes=lead) <= now
            <= booking.slot.end_at + timedelta(minutes=grace)):
        return _json_error('Вне окна урока', status=409)

    event = (request.POST.get('event') or '').strip()
    if event == 'join':
        try:
            booking.record_join(is_teacher=is_teacher)
        except Exception:
            logger.warning('record_join failed for booking %s', booking.pk, exc_info=True)
    elif event == 'leave':
        # seconds от клиента больше не используем — сервер авторитетно закрывает
        # интервал присутствия (LessonAttendanceSession) по своему времени.
        try:
            booking.record_leave(is_teacher=is_teacher)
        except Exception:
            logger.warning('record_leave failed for booking %s', booking.pk, exc_info=True)
    else:
        return _json_error('Неизвестное событие', status=400)
    return JsonResponse({'ok': True})


# Логгер диагностики комнаты урока. Пишем в обычный лог (greppable по booking_id),
# без отдельной таблицы/миграции — это телеметрия связи, а не аудит-журнал денег.
_diag_logger = logging.getLogger('lesson.diag')

# Белый список технических событий клиента (защита от мусора/инъекций в лог).
_DIAG_EVENTS = {
    'reconnect', 'reconnect_giveup', 'fatal_error', 'no_audio_input',
    'net_offline', 'net_online', 'upload_failed', 'load_error',
}


@authenticated_required
@require_http_methods(['POST'])
def lesson_diag_api(request, booking_id):
    """Диагностика комнаты урока: редкие критические события связи/устройств.

    Через navigator.sendBeacon, только на нечастые события (переподключение,
    фатальная ошибка, мёртвый микрофон, провал загрузки файла). Нагрузки не
    создаёт; пишет в лог 'lesson.diag' для разбора инцидентов по booking_id.
    Body (form): event=<whitelisted>, detail=<str, опц.>.
    """
    booking = get_object_or_404(
        Booking.objects.select_related('slot__teacher'), pk=booking_id,
    )
    user = request.user
    tp = getattr(user, 'teacher_profile', None)
    is_teacher = bool(tp and booking.slot.teacher_id == tp.pk)
    is_student = (booking.student_id == user.pk)
    if not (is_teacher or is_student):
        return _json_error('Доступ запрещён', status=403)

    event = (request.POST.get('event') or '').strip()[:40]
    if event not in _DIAG_EVENTS:
        # Тихо игнорируем неизвестные коды — beacon не должен шуметь ошибками.
        return JsonResponse({'ok': True})
    detail = (request.POST.get('detail') or '').strip()[:200]
    _diag_logger.info(
        'booking=%s role=%s event=%s detail=%s',
        booking.pk, ('teacher' if is_teacher else 'student'), event, detail,
    )
    return JsonResponse({'ok': True})


@authenticated_required
def leave_review(request, booking_id):
    """
    Оставить / обновить отзыв по завершённому уроку.

    Доступно только ученику этой брони и только если урок completed.
    Уважает unique_together(teacher, student, subject): повторный отзыв
    тому же учителю по тому же предмету — редактирует существующий.
    """
    booking = get_object_or_404(
        Booking.objects.select_related('slot__teacher__user', 'student', 'subject'),
        pk=booking_id,
    )
    if booking.student_id != request.user.pk:
        return HttpResponseForbidden('Отзыв может оставить только ученик этого урока.')
    if booking.status != 'completed':
        messages.warning(request, _('Оставить отзыв можно только после завершения урока.'))
        return redirect('my_bookings_page')

    teacher = booking.slot.teacher
    # Per-booking Review (OneToOne booking) — один отзыв = один урок.
    existing = Review.objects.filter(booking=booking).first()

    if request.method == 'POST':
        def _clamp(name, default=5):
            try:
                v = int(request.POST.get(name, default))
            except (TypeError, ValueError):
                v = default
            return max(1, min(5, v))

        rating = _clamp('rating')
        knowledge = _clamp('knowledge_rating', rating)
        communication = _clamp('communication_rating', rating)
        punctuality = _clamp('punctuality_rating', rating)
        comment = (request.POST.get('comment') or '').strip()[:1000]
        # Отзыв публичен — контакты маскируем ВСЕГДА (без порога доверия),
        # чтобы нельзя было оставить телефон/мессенджер в открытом тексте.
        if comment:
            from .contact_filter import mask_contacts
            # NB: не использовать `_` для распаковки — это затеняет gettext `_`,
            # из-за чего messages.success(_('…')) ниже падал TypeError.
            comment, _masked = mask_contacts(comment)

        review = existing or Review(teacher=teacher, student=request.user, subject=booking.subject, booking=booking)
        review.rating = rating
        review.knowledge_rating = knowledge
        review.communication_rating = communication
        review.punctuality_rating = punctuality
        review.comment = comment
        review.is_verified = True
        review.save()

        messages.success(request, _('Спасибо! Ваш отзыв сохранён.'))
        # Если пришли из подписки — возвращаем на /my/subscriptions/
        next_url = request.POST.get('next') or request.GET.get('next')
        if next_url and next_url.startswith('/'):
            return redirect(next_url)
        return redirect('teacher_detail', id=teacher.pk)

    return render(request, 'booking/leave_review.html', {
        'booking': booking,
        'teacher': teacher,
        'existing': existing,
    })


# ---------------------------------------------------------------- HELPERS --

def _notify_teacher_about_booking(booking: Booking):
    """Создаёт Notification для учителя + WS push."""
    try:
        from .models import Notification, User as UserModel
        teacher_user = booking.slot.teacher.user
        student_name = booking.student.get_full_name() or booking.student.username
        slot_str = booking.slot.start_at.strftime('%d.%m %H:%M')
        deadline_str = timezone.localtime(booking.expires_at).strftime('%d.%m %H:%M') if booking.expires_at else slot_str
        Notification.objects.create(
            title='Новое бронирование',
            short_text=f'{student_name} забронировал слот {slot_str}',
            full_text=(
                f'Ученик {student_name} запросил бронирование на {slot_str}.\n\n'
                f'Сообщение: {booking.student_message or "—"}\n\n'
                f'Подтвердите или отклоните до {deadline_str} (не позднее чем за 5 минут до начала урока), '
                f'иначе слот снова станет свободным.'
            ),
            target='specific_user',
            target_user=teacher_user,
            priority=10,
            is_active=True,
            category=Notification.Category.BOOKING,
            booking=booking,
        )
        # Notification post_save сигнал сам пушит WS — у нас уже есть push_notification_realtime
    except Exception as e:
        logger.warning(f'_notify_teacher_about_booking failed: {e}')


def _notify_student_about_decision(booking: Booking, decision: str):
    """decision: confirmed / rejected / cancelled."""
    try:
        from .models import Notification
        slot_str = booking.slot.start_at.strftime('%d.%m %H:%M')
        teacher_name = booking.slot.teacher.user.get_full_name() or booking.slot.teacher.user.username
        if decision == 'confirmed':
            title = 'Урок подтверждён'
            short = f'Учитель {teacher_name} подтвердил урок на {slot_str}'
            text = f'Ваш урок с {teacher_name} на {slot_str} подтверждён.'
            category = Notification.Category.SUCCESS
            if booking.teacher_reply:
                text += f'\n\nСообщение учителя: {booking.teacher_reply}'
        elif decision == 'rejected':
            title = 'Бронирование отклонено'
            short = f'Учитель {teacher_name} отклонил вашу заявку на {slot_str}'
            text = f'Учитель {teacher_name} не может провести урок на {slot_str}.'
            category = Notification.Category.WARNING
            if booking.teacher_reply:
                text += f'\n\nСообщение: {booking.teacher_reply}'
        else:  # cancelled
            title = 'Урок отменён учителем'
            short = f'Учитель отменил урок на {slot_str}'
            text = f'Учитель {teacher_name} отменил подтверждённый ранее урок на {slot_str}.'
            category = Notification.Category.WARNING
        Notification.objects.create(
            title=title, short_text=short, full_text=text,
            target='specific_user', target_user=booking.student,
            priority=10, is_active=True, category=category,
        )
    except Exception as e:
        logger.warning(f'_notify_student_about_decision failed: {e}')

    # Отдельный WS-event для live-обновления открытой booking-modal у студента.
    # Notification идёт своим путём (toast + badge); booking_status — узкий канал
    # для UI-страниц, которые ждут конкретного booking.
    try:
        from .consumers import notify_user
        notify_user(booking.student_id, 'booking_status_changed', {
            'booking_id': str(booking.id),
            'status': booking.status,
            'decision': decision,
            'meeting_url': booking.meeting_url or '',
            # Ведём в нашу комнату урока (учёт присутствия/спор/доска), а не на
            # сырой Jitsi: иначе вход мимо beacon → возможна ложная неявка.
            'lesson_room_url': reverse('lesson_room', args=[booking.id]) if booking.meeting_url else '',
            'teacher_reply': booking.teacher_reply or '',
        })
    except Exception as e:
        logger.warning(f'booking_status_changed WS push failed: {e}')


def _notify_teacher_about_cancellation(booking: Booking, by: str):
    """Студент отменил — учитель должен знать."""
    try:
        from .models import Notification
        slot_str = booking.slot.start_at.strftime('%d.%m %H:%M')
        student_name = booking.student.get_full_name() or booking.student.username
        Notification.objects.create(
            title='Ученик отменил бронирование',
            short_text=f'{student_name} отменил слот {slot_str}',
            full_text=f'Ученик {student_name} отменил бронирование на {slot_str}. Слот снова свободен.',
            target='specific_user', target_user=booking.slot.teacher.user,
            priority=5, is_active=True, category=Notification.Category.BOOKING,
        )
    except Exception as e:
        logger.warning(f'_notify_teacher_about_cancellation failed: {e}')
