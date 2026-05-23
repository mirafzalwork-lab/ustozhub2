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
from django.db import transaction
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
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
            messages.warning(request, 'Сначала завершите профиль учителя.')
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


def _json_error(message: str, status: int = 400):
    return JsonResponse({'error': message}, status=status)


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

    slot = TimeSlot.objects.create(
        teacher=request.teacher_profile,
        start_at=start,
        end_at=end,
        status=status,
    )
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
    slot.save(update_fields=['start_at', 'end_at', 'status', 'updated_at'])

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
                    TimeSlot.objects.create(
                        teacher=teacher,
                        start_at=cursor,
                        end_at=slot_end,
                        status='free',
                    )
                    existing.append((cursor, slot_end))  # для последующих проверок в том же запуске
                    created += 1
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
    Body: {from: ISO datetime, to: ISO datetime, only_free?: bool=true}

    Защита: only_free=true (default) — не трогаем held/booked.
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

    only_free = data.get('only_free', True)

    qs = TimeSlot.objects.filter(
        teacher=request.teacher_profile,
        start_at__gte=start,
        start_at__lt=end,
    )
    if only_free:
        qs = qs.filter(status='free')

    count = qs.count()
    qs.delete()

    logger.info(f'bulk_delete: teacher={request.teacher_profile.pk} '
                f'range={start}..{end} only_free={only_free} deleted={count}')
    return JsonResponse({'deleted': count, 'only_free': only_free})


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
    return {
        'id': str(b.id),
        'status': b.status,
        'status_display': b.get_status_display(),
        'is_trial': b.is_trial,
        'created_at': b.created_at.isoformat(),
        'expires_at': b.expires_at.isoformat() if b.expires_at else None,
        'student_message': b.student_message,
        'teacher_reply': b.teacher_reply,
        'meeting_url': b.meeting_url,
        # Внутренняя комната (встроенный Jitsi) — для авто-комнат; для кастомных
        # внешних ссылок meeting_is_jitsi=False и заходим напрямую по meeting_url.
        'meeting_is_jitsi': b.is_jitsi_meeting(),
        'lesson_room_url': reverse('lesson_room', args=[b.id]),
        # Отзыв: ученик может оценить завершённый урок (или обновить отзыв)
        'review_url': reverse('leave_review', args=[b.id]),
        'has_review': has_review,
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
    is_trial = bool(data.get('is_trial', False))

    try:
        booking = Booking.create_hold(
            slot_id=slot_id,
            student=request.user,
            subject=subject,
            message=message,
            is_trial=is_trial,
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
                booking.reject(teacher_reply=(json.loads(request.body or '{}').get('reply', '')))
            else:
                # confirmed → cancelled_by_teacher
                booking.status = 'cancelled_by_teacher'
                booking.save(update_fields=['status', 'updated_at'])
                booking.slot.status = 'free'
                booking.slot.hold_expires_at = None
                booking.slot.save(update_fields=['status', 'hold_expires_at', 'updated_at'])
            _notify_student_about_decision(booking, decision='cancelled')
        else:
            return _json_error('Доступ запрещён', status=403)
    except ValueError as e:
        return _json_error(str(e), status=409)

    return JsonResponse({'booking': _booking_to_dict(booking)})


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
        booking.reschedule_by_student(new_slot_id)
    except SlotUnavailable as e:
        return _json_error(f'Слот недоступен: {e}', status=409)
    except TimeSlot.DoesNotExist:
        return _json_error('Слот не найден', status=404)
    except ValueError as e:
        return _json_error(str(e), status=409)

    # Уведомляем учителя о новой заявке на подтверждение
    _notify_teacher_about_booking(booking)
    logger.info(f'Booking rescheduled: {booking.pk} → slot={new_slot_id}')
    return JsonResponse({'booking': _booking_to_dict(booking)})


# ---------------------------------------------------------------- TEACHER --

_MEETING_URL_MAX_LEN = 500
_MEETING_URL_ALLOWED_SCHEMES = ('https://', 'http://')


def _validate_meeting_url(url: str) -> tuple[bool, str]:
    """
    Простая валидация URL для встречи: должен быть http(s), длина ≤ 500,
    выглядит как URL. Возвращает (ok, error_msg).
    """
    url = (url or '').strip()
    if not url:
        return True, ''  # пусто — это ок, учитель может оставить пустым
    if len(url) > _MEETING_URL_MAX_LEN:
        return False, f'URL слишком длинный (макс {_MEETING_URL_MAX_LEN})'
    if not url.lower().startswith(_MEETING_URL_ALLOWED_SCHEMES):
        return False, 'Ссылка должна начинаться с https:// или http://'
    # Минимальная sanity-проверка: должен быть хост
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        if not parsed.netloc or '.' not in parsed.netloc:
            return False, 'Некорректный URL'
    except Exception:
        return False, 'Некорректный URL'
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

    try:
        booking.reject(teacher_reply=reply)
    except ValueError as e:
        return _json_error(str(e), status=409)

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
        'slot__teacher__user', 'student', 'subject'
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
        messages.error(request, 'Учитель не найден')
        return redirect('home')

    # Подгружаем предметы учителя для select
    teacher_subjects = list(
        teacher.teachersubject_set.select_related('subject')
        .values('subject__id', 'subject__name', 'hourly_rate', 'is_free_trial')
    )

    return render(request, 'booking/book_teacher.html', {
        'teacher': teacher,
        'teacher_subjects': teacher_subjects,
    })


@authenticated_required
def lesson_room(request, booking_id):
    """
    Встроенная видео-комната урока (Jitsi через iframe), внутри платформы.

    Доступ только у учителя и ученика этой брони. Открывается с -15 минут до
    начала и до +30 минут после конца. Кастомные внешние ссылки (Zoom и т.п.)
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
    open_from = booking.slot.start_at - timedelta(minutes=15)
    open_until = booking.slot.end_at + timedelta(minutes=30)

    state = 'ok'
    if booking.status != 'confirmed':
        state = 'not_confirmed'
    elif now < open_from:
        state = 'too_early'
    elif now > open_until:
        state = 'too_late'

    jitsi_base = (getattr(settings, 'JITSI_BASE_URL', 'https://meet.jit.si') or '').rstrip('/')
    # Домен-хост для External API (без схемы)
    jitsi_domain = jitsi_base.split('://', 1)[-1]

    other = booking.slot.teacher.user if is_student else booking.student
    other_name = other.get_full_name() or other.username

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
    })


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
        messages.warning(request, 'Оставить отзыв можно только после завершения урока.')
        return redirect('my_bookings_page')

    teacher = booking.slot.teacher
    # Существующий отзыв этого ученика этому учителю по этому предмету
    existing = Review.objects.filter(
        teacher=teacher, student=request.user, subject=booking.subject,
    ).first()

    if request.method == 'POST':
        def _clamp(name, default=5):
            try:
                v = int(request.POST.get(name, default))
            except (TypeError, ValueError):
                v = default
            return max(1, min(5, v))

        rating = _clamp('rating')
        # Детальные оценки опциональны — по умолчанию равны общей
        knowledge = _clamp('knowledge_rating', rating)
        communication = _clamp('communication_rating', rating)
        punctuality = _clamp('punctuality_rating', rating)
        comment = (request.POST.get('comment') or '').strip()[:1000]

        review = existing or Review(teacher=teacher, student=request.user, subject=booking.subject)
        review.rating = rating
        review.knowledge_rating = knowledge
        review.communication_rating = communication
        review.punctuality_rating = punctuality
        review.comment = comment
        review.booking = booking
        review.is_verified = True
        review.save()

        messages.success(request, 'Спасибо! Ваш отзыв сохранён.')
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
                f'Подтвердите или отклоните до {deadline_str} (за час до начала урока), '
                f'иначе слот снова станет свободным.'
            ),
            target='specific_user',
            target_user=teacher_user,
            priority=10,
            is_active=True,
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
            title = '✅ Урок подтверждён'
            short = f'Учитель {teacher_name} подтвердил урок на {slot_str}'
            text = f'Ваш урок с {teacher_name} на {slot_str} подтверждён.'
            if booking.teacher_reply:
                text += f'\n\nСообщение учителя: {booking.teacher_reply}'
        elif decision == 'rejected':
            title = '❌ Бронирование отклонено'
            short = f'Учитель {teacher_name} отклонил вашу заявку на {slot_str}'
            text = f'Учитель {teacher_name} не может провести урок на {slot_str}.'
            if booking.teacher_reply:
                text += f'\n\nСообщение: {booking.teacher_reply}'
        else:  # cancelled
            title = 'Урок отменён учителем'
            short = f'Учитель отменил урок на {slot_str}'
            text = f'Учитель {teacher_name} отменил подтверждённый ранее урок на {slot_str}.'
        Notification.objects.create(
            title=title, short_text=short, full_text=text,
            target='specific_user', target_user=booking.student,
            priority=10, is_active=True,
        )
    except Exception as e:
        logger.warning(f'_notify_student_about_decision failed: {e}')


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
            priority=5, is_active=True,
        )
    except Exception as e:
        logger.warning(f'_notify_teacher_about_cancellation failed: {e}')
