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

from .models import TeacherProfile, TimeSlot, Booking, SlotUnavailable, Subject

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


def _booking_to_dict(b: Booking) -> dict:
    """Сериализация бронирования для JSON-ответов."""
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
    """
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
    """
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
    logger.info(f'Booking confirmed: {booking.pk}, meeting_url={"set" if meeting_url else "empty"}')
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
    return JsonResponse({'bookings': [_booking_to_dict(b) for b in qs]})


@authenticated_required
def my_bookings_page(request):
    """Страница 'Мои бронирования / Уроки'."""
    return render(request, 'booking/my_bookings.html', {
        'role': request.user.user_type,
    })


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


# ---------------------------------------------------------------- HELPERS --

def _notify_teacher_about_booking(booking: Booking):
    """Создаёт Notification для учителя + WS push."""
    try:
        from .models import Notification, User as UserModel
        teacher_user = booking.slot.teacher.user
        student_name = booking.student.get_full_name() or booking.student.username
        slot_str = booking.slot.start_at.strftime('%d.%m %H:%M')
        Notification.objects.create(
            title='Новое бронирование',
            short_text=f'{student_name} забронировал слот {slot_str}',
            full_text=(
                f'Ученик {student_name} запросил бронирование на {slot_str}.\n\n'
                f'Сообщение: {booking.student_message or "—"}\n\n'
                f'Подтвердите или отклоните в течение 15 минут, иначе слот '
                f'снова станет свободным.'
            ),
            target='specific_user',
            target_user=teacher_user,
            priority=10,
            is_active=True,
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
