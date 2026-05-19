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

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods

from .models import TeacherProfile, TimeSlot

logger = logging.getLogger(__name__)


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

    booking_info = None
    if hasattr(slot, 'booking'):
        b = slot.booking
        booking_info = {
            'id': str(b.id),
            'student_name': b.student.get_full_name() or b.student.username,
            'status': b.status,
            'status_display': b.get_status_display(),
            'is_trial': b.is_trial,
            'student_message': b.student_message[:200],
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


# --------------------------------------------------------------------------- #
# UI page
# --------------------------------------------------------------------------- #

@teacher_required
def teacher_calendar(request):
    """Страница календаря учителя."""
    return render(request, 'booking/teacher_calendar.html', {
        'teacher': request.teacher_profile,
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

    slots = (
        TimeSlot.objects
        .filter(teacher=request.teacher_profile, start_at__gte=start, start_at__lt=end)
        .select_related('booking', 'booking__student')
        .order_by('start_at')
    )
    return JsonResponse({'events': [_slot_to_event(s) for s in slots]})


@teacher_required
@require_http_methods(['POST'])
def slots_create_api(request):
    """Создать новый слот. Body: {start, end, status?}."""
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

    # Проверяем пересечение с существующими слотами
    overlap = TimeSlot.objects.filter(
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
def slots_detail_api(request, slot_id: int):
    """Изменить (drag/resize) или удалить слот."""
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

    with transaction.atomic():
        slot.start_at = start
        slot.end_at = end
        slot.status = new_status
        slot.save(update_fields=['start_at', 'end_at', 'status', 'updated_at'])

    return JsonResponse({'event': _slot_to_event(slot)})
