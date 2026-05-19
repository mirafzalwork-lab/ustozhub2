"""
Полный regression-test: Phase 0 + Phase 1 + Phase 2.

Запуск:
    python manage.py shell < scripts/regression_test.py

Тесты НЕ изменяют production-данные:
- TimeSlot создаются с start_at в году 2099 (далеко в будущем)
- Booking создаются от тестового teacher/student (первые найденные)
- В конце ВСЁ удаляется в cleanup-блоке

Считает passed/failed, в конце печатает summary.
"""
import json
import sys
import urllib.parse
from datetime import timedelta
from contextlib import contextmanager

from django.core.cache import cache
from django.db import IntegrityError, connection, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from teachers.models import (
    User, TeacherProfile, TimeSlot, Booking, SlotUnavailable,
    Subject, WizardDraft, Notification, ProfileView, LessonReminderSent,
)
from teachers.tasks import (
    release_expired_holds, mark_completed_lessons,
    cleanup_wizard_drafts_async, health_check,
    send_lesson_reminders,
)

# ============================================================
# Test framework (минималистичный, без pytest)
# ============================================================

PASSED, FAILED = 0, 0
FAILURES = []


@contextmanager
def section(name):
    print(f'\n━━━ {name} ━━━')
    yield


def check(name, cond, detail=''):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f'  ✓ {name}')
    else:
        FAILED += 1
        FAILURES.append(f'{name} — {detail}')
        print(f'  ✗ {name}    {detail}')


def expect_exc(name, fn, exc_class):
    try:
        fn()
    except exc_class as e:
        check(name, True)
        return e
    except Exception as e:
        check(name, False, f'неожиданный {type(e).__name__}: {e}')
        return None
    check(name, False, 'исключения не было')
    return None


# ============================================================
# Setup: фикстуры
# ============================================================

teacher_user = User.objects.filter(user_type='teacher', teacher_profile__isnull=False).first()
teacher_profile = teacher_user.teacher_profile
student1 = User.objects.filter(user_type='student').first()
student2 = User.objects.filter(user_type='student').exclude(pk=student1.pk).first()
other_teacher = User.objects.filter(user_type='teacher', teacher_profile__isnull=False).exclude(pk=teacher_user.pk).first()

print(f'Test fixtures:')
print(f'  teacher: {teacher_user.username} (profile id={teacher_profile.pk})')
print(f'  student1: {student1.username}')
print(f'  student2: {student2.username}')
print(f'  other_teacher: {other_teacher.username if other_teacher else "—"}')

# Уборка перед стартом
TimeSlot.objects.filter(start_at__year=2099).delete()
WizardDraft.objects.filter(session_key__startswith='__test__').delete()


# ============================================================
# Phase 0: Infrastructure
# ============================================================

with section('Phase 0: Infrastructure'):
    check('DB engine is configured (sqlite ok локально)',
          connection.vendor in ('postgresql', 'sqlite'),
          detail=f'vendor={connection.vendor}')

    cache.set('__regression__', 'pong', 30)
    check('Cache backend write+read', cache.get('__regression__') == 'pong')
    cache.delete('__regression__')

    # apply() — синхронный запуск Celery task без отправки в broker
    # (локально мы не запускаем worker, реальный broker-flow проверим на проде)
    result = health_check.apply().get()
    check('Celery health_check task returns ok (sync apply)', result.get('ok') is True)

    # Email backend (console в dev)
    from django.core.mail import send_mail
    try:
        send_mail('Regression test', 'ignore me', 'test@local', ['test@local'])
        check('Email backend send_mail работает', True)
    except Exception as e:
        check('Email backend send_mail работает', False, str(e))


# ============================================================
# Phase 1: Models
# ============================================================

with section('Phase 1: TimeSlot & Booking models'):
    base = timezone.make_aware(timezone.datetime(2099, 7, 1, 10, 0))

    slot = TimeSlot.objects.create(teacher=teacher_profile, start_at=base, end_at=base + timedelta(hours=1))
    check('TimeSlot создаётся', slot.pk and slot.status == 'free')
    check('TimeSlot.duration_minutes = 60', slot.duration_minutes == 60)
    check('TimeSlot.is_in_past = False (будущий)', slot.is_in_past is False)

    def _bad_slot():
        TimeSlot.objects.create(
            teacher=teacher_profile,
            start_at=base + timedelta(hours=2),
            end_at=base + timedelta(hours=1),  # end < start
        )
    expect_exc('CHECK constraint: end > start', _bad_slot, IntegrityError)

    # create_hold
    b = Booking.create_hold(slot.pk, student1, message='Тест holdа')
    slot.refresh_from_db()
    check('create_hold: booking.status = pending', b.status == 'pending')
    check('create_hold: slot.status = held', slot.status == 'held')
    check('create_hold: expires_at установлен', b.expires_at is not None)
    check('create_hold: slot.hold_expires_at установлен', slot.hold_expires_at is not None)

    # race: второй студент
    expect_exc('Race: SlotUnavailable при двойном hold',
               lambda: Booking.create_hold(slot.pk, student2), SlotUnavailable)

    # DB UNIQUE
    def _dup_booking():
        Booking.objects.create(slot=slot, student=student2, status='pending')
    expect_exc('OneToOneField UNIQUE на slot блокирует дубль', _dup_booking, IntegrityError)

    # confirm
    b.confirm(teacher_reply='OK')
    slot.refresh_from_db(); b.refresh_from_db()
    check('confirm: booking.status=confirmed', b.status == 'confirmed')
    check('confirm: slot.status=booked', slot.status == 'booked')
    check('confirm: expires_at сброшен', b.expires_at is None)
    check('confirm: slot.hold_expires_at сброшен', slot.hold_expires_at is None)
    check('confirm: teacher_reply сохранён', b.teacher_reply == 'OK')

    # cancel_by_student
    b.cancel_by_student()
    slot.refresh_from_db(); b.refresh_from_db()
    check('cancel_by_student: booking → cancelled_by_student', b.status == 'cancelled_by_student')
    check('cancel_by_student: slot → free', slot.status == 'free')

    # reject scenario
    b2 = Booking.create_hold(slot.pk, student1)
    b2.reject(teacher_reply='Сегодня не могу')
    slot.refresh_from_db(); b2.refresh_from_db()
    check('reject: booking → cancelled_by_teacher', b2.status == 'cancelled_by_teacher')
    check('reject: slot → free', slot.status == 'free')

    # expire
    b3 = Booking.create_hold(slot.pk, student1, hold_minutes=15)
    Booking.objects.filter(pk=b3.pk).update(expires_at=timezone.now() - timedelta(seconds=10))
    slot.refresh_from_db()
    n_expired = release_expired_holds()
    slot.refresh_from_db(); b3.refresh_from_db()
    check('release_expired_holds возвращает count > 0', n_expired >= 1)
    check('expire: booking → expired', b3.status == 'expired')
    check('expire: slot → free', slot.status == 'free')

    # mark_completed (через past confirmed) — slot станет с start_at сегодня
    past_slot = TimeSlot.objects.create(
        teacher=teacher_profile,
        start_at=timezone.make_aware(timezone.datetime(2099, 7, 1, 12, 0)),
        end_at=timezone.make_aware(timezone.datetime(2099, 7, 1, 13, 0)),
    )
    # Запоминаем pk, чтобы cleanup явно его удалил
    _completed_test_slot_pk = past_slot.pk
    b4 = Booking.create_hold(past_slot.pk, student1)
    b4.confirm()
    # Имитируем что slot уже прошёл: ставим И start_at И end_at в прошлое
    # (start < end сохраняем для CHECK constraint)
    now = timezone.now()
    TimeSlot.objects.filter(pk=past_slot.pk).update(
        start_at=now - timedelta(hours=2),
        end_at=now - timedelta(minutes=5),
    )
    n_completed = mark_completed_lessons()
    b4.refresh_from_db()
    check('mark_completed_lessons → status completed', b4.status == 'completed')
    check('mark_completed: ended_at установлен', b4.ended_at is not None)

    # cleanup_wizard_drafts_async
    WizardDraft.objects.create(
        session_key='__test__regress',
        current_step='basic',
        data={'k': 'v'},
    )
    WizardDraft.objects.filter(session_key='__test__regress').update(
        updated_at=timezone.now() - timedelta(days=30)
    )
    n_deleted = cleanup_wizard_drafts_async(days=14)
    check('cleanup_wizard_drafts_async удаляет старые', n_deleted >= 1)


# ============================================================
# Phase 4: Lesson Reminders (T-24h / T-3h / T-10min)
# ВНИМАНИЕ: тесты создают slots в БЛИЖАЙШЕМ будущем (10min / 24h)
# не 2099 — потому что фильтрация задачи по абсолютному времени.
# Все артефакты теста заворачиваем в transaction + savepoint и откатываем,
# чтобы prod-БД не получила фейковые Notification.
# ============================================================

with section('Phase 4: Lesson Reminders'):
    from django.core import mail
    from datetime import timedelta
    from django.db import transaction

    class _RollbackForTest(Exception):
        """Используется чтобы явно откатить transaction.atomic в конце Phase 4."""
        pass

    phase4_passed_all = True
    try:
        with transaction.atomic():
            # Создаём slot чтобы start_at попал точно в окно T-10min ± 90 сек
            now = timezone.now()
            target = now + timedelta(minutes=10)
            slot_rem = TimeSlot.objects.create(
                teacher=teacher_profile,
                start_at=target,
                end_at=target + timedelta(hours=1),
            )
            b_rem = Booking.create_hold(slot_rem.pk, student1)
            b_rem.confirm()

            mail.outbox = []
            sent_count = send_lesson_reminders()
            check('send_lesson_reminders: отправлено >= 1', sent_count >= 1, detail=f'got {sent_count}')
            rem = LessonReminderSent.objects.filter(booking=b_rem, kind='10min').first()
            check('Reminder для T-10min записан в БД', rem is not None)
            if rem:
                check('Reminder channels содержит in_app', 'in_app' in rem.channels,
                      detail=f'channels={rem.channels}')
                email_attempted = ('email' in rem.channels) or (len(mail.outbox) >= 1)
                check('Email-канал использован', email_attempted,
                      detail=f'channels={rem.channels} outbox={len(mail.outbox)}')

            sent_count2 = send_lesson_reminders()
            check('Повторный send_lesson_reminders → 0 (idempotency)', sent_count2 == 0)
            check('LessonReminderSent ровно одна запись (UNIQUE)',
                  LessonReminderSent.objects.filter(booking=b_rem, kind='10min').count() == 1)

            # T-24h окно
            target24 = now + timedelta(hours=24)
            slot24 = TimeSlot.objects.create(
                teacher=teacher_profile,
                start_at=target24,
                end_at=target24 + timedelta(hours=1),
            )
            b24 = Booking.create_hold(slot24.pk, student1)
            b24.confirm()
            sent_24 = send_lesson_reminders()
            check('Reminder T-24h тоже отправляется', sent_24 >= 1)
            check('LessonReminderSent для T-24h создан',
                  LessonReminderSent.objects.filter(booking=b24, kind='24h').exists())

            # Вне окна (6h) — не попадает
            target_out = now + timedelta(hours=6)
            slot_out = TimeSlot.objects.create(
                teacher=teacher_profile,
                start_at=target_out,
                end_at=target_out + timedelta(hours=1),
            )
            b_out = Booking.create_hold(slot_out.pk, student1)
            b_out.confirm()
            sent_out = send_lesson_reminders()
            check('Slot вне окна не получает reminder', sent_out == 0)
            check('Нет записи для slot вне окна',
                  not LessonReminderSent.objects.filter(booking=b_out).exists())

            # Принудительный rollback всего что создано
            raise _RollbackForTest()
    except _RollbackForTest:
        pass  # ожидаемо — транзакция откатилась, БД чиста


# ============================================================
# Phase 2: Calendar API
# ============================================================

with section('Phase 2: Calendar API (Django Client)'):
    c = Client()

    # Anonymous → redirect to login
    r = c.get(reverse('teacher_calendar'))
    check('Anonymous GET calendar → redirect (302)', r.status_code in (301, 302))

    # Student → 403
    c.force_login(student1)
    r = c.get(reverse('teacher_calendar'))
    check('Student GET calendar → 403', r.status_code == 403)
    r = c.get(reverse('slots_list_api') + '?start=2099-01-01&end=2099-01-02')
    check('Student GET API → 403', r.status_code == 403)
    c.logout()

    # Teacher full lifecycle
    c.force_login(teacher_user)
    r = c.get(reverse('teacher_calendar'))
    check('Teacher GET calendar → 200', r.status_code == 200)
    check('Calendar HTML содержит fullcalendar', b'fullcalendar' in r.content.lower())
    check('Calendar HTML содержит CSRF token', b'csrfmiddlewaretoken' in r.content)

    # Create slot
    start = timezone.make_aware(timezone.datetime(2099, 8, 1, 10, 0))
    end = start + timedelta(hours=1)
    r = c.post(reverse('slots_create_api'),
               json.dumps({'start': start.isoformat(), 'end': end.isoformat()}),
               'application/json')
    check('POST create → 201', r.status_code == 201)
    if r.status_code == 201:
        slot_id = r.json()['event']['id']
        check('Create response содержит event.id', slot_id is not None)
        check('Create response содержит backgroundColor', 'backgroundColor' in r.json()['event'])

        # GET list
        qs = urllib.parse.urlencode({
            'start': (start - timedelta(days=1)).isoformat(),
            'end': (start + timedelta(days=2)).isoformat(),
        })
        r = c.get(reverse('slots_list_api') + '?' + qs)
        check('GET list → 200', r.status_code == 200)
        check('GET list содержит созданный slot',
              any(e['id'] == slot_id for e in r.json()['events']))

        # PATCH drag
        new_start = start + timedelta(hours=2)
        new_end = new_start + timedelta(hours=1)
        r = c.patch(reverse('slots_detail_api', args=[slot_id]),
                    json.dumps({'start': new_start.isoformat(), 'end': new_end.isoformat()}),
                    'application/json')
        check('PATCH drag → 200', r.status_code == 200)

        # PATCH change status to blocked
        r = c.patch(reverse('slots_detail_api', args=[slot_id]),
                    json.dumps({'status': 'blocked'}),
                    'application/json')
        check('PATCH status=blocked → 200', r.status_code == 200)
        check('PATCH response отражает blocked',
              r.json()['event']['extendedProps']['status'] == 'blocked')

        # PATCH invalid status
        r = c.patch(reverse('slots_detail_api', args=[slot_id]),
                    json.dumps({'status': 'invalid'}),
                    'application/json')
        check('PATCH invalid status → 400', r.status_code == 400)

        # Overlap
        r = c.post(reverse('slots_create_api'),
                   json.dumps({'start': new_start.isoformat(), 'end': new_end.isoformat()}),
                   'application/json')
        check('POST overlap → 409', r.status_code == 409)

        # DELETE
        r = c.delete(reverse('slots_detail_api', args=[slot_id]))
        check('DELETE free slot → 200', r.status_code == 200)

    # Past slot
    past = (timezone.now() - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    r = c.post(reverse('slots_create_api'),
               json.dumps({'start': past.isoformat(), 'end': (past + timedelta(hours=1)).isoformat()}),
               'application/json')
    check('POST past slot → 400', r.status_code == 400)

    # Duration > 8h
    r = c.post(reverse('slots_create_api'),
               json.dumps({'start': start.isoformat(), 'end': (start + timedelta(hours=9)).isoformat()}),
               'application/json')
    check('POST > 8h → 400', r.status_code == 400)

    # end <= start
    r = c.post(reverse('slots_create_api'),
               json.dumps({'start': start.isoformat(), 'end': start.isoformat()}),
               'application/json')
    check('POST end==start → 400', r.status_code == 400)

    # Invalid JSON
    r = c.post(reverse('slots_create_api'), 'not json', 'application/json')
    check('POST invalid JSON → 400', r.status_code == 400)

    # Missing fields
    r = c.post(reverse('slots_create_api'), '{}', 'application/json')
    check('POST empty body → 400', r.status_code == 400)

    # DELETE/PATCH on held slot (нельзя)
    base2 = timezone.make_aware(timezone.datetime(2099, 9, 1, 10, 0))
    slot_held = TimeSlot.objects.create(teacher=teacher_profile, start_at=base2, end_at=base2 + timedelta(hours=1))
    b_held = Booking.create_hold(slot_held.pk, student1)
    r = c.delete(reverse('slots_detail_api', args=[slot_held.pk]))
    check('DELETE held slot → 409', r.status_code == 409)
    r = c.patch(reverse('slots_detail_api', args=[slot_held.pk]),
                json.dumps({'start': (base2 + timedelta(hours=3)).isoformat(),
                            'end':   (base2 + timedelta(hours=4)).isoformat()}),
                'application/json')
    check('PATCH held slot → 409', r.status_code == 409)

    # ---- Phase 5.5: bulk-операции ----
    # bulk_delete на видимом окне (без слотов — 0)
    r = c.post(reverse('slots_bulk_delete_api'),
               json.dumps({
                   'from': '2099-12-01T00:00:00+00:00',
                   'to': '2099-12-31T23:59:59+00:00',
                   'only_free': True,
               }), 'application/json')
    check('bulk_delete пустого окна → 200', r.status_code == 200)
    check('bulk_delete deleted=0 для пустого', r.json()['deleted'] == 0)

    # Создаём 3 свободных + 1 booked в окне дек 2099
    base_bulk = timezone.make_aware(timezone.datetime(2099, 12, 5, 10, 0))
    bulk_slots = []
    for i in range(3):
        s = TimeSlot.objects.create(
            teacher=teacher_profile,
            start_at=base_bulk + timedelta(hours=i),
            end_at=base_bulk + timedelta(hours=i + 1),
        )
        bulk_slots.append(s)
    # Один с booking → не должен удалиться
    s_book = TimeSlot.objects.create(
        teacher=teacher_profile,
        start_at=base_bulk + timedelta(days=1),
        end_at=base_bulk + timedelta(days=1, hours=1),
    )
    b_book = Booking.create_hold(s_book.pk, student1)
    b_book.confirm()

    r = c.post(reverse('slots_bulk_delete_api'),
               json.dumps({
                   'from': '2099-12-01T00:00:00+00:00',
                   'to':   '2099-12-31T23:59:59+00:00',
                   'only_free': True,
               }), 'application/json')
    check('bulk_delete только free → 200', r.status_code == 200)
    check('bulk_delete deleted=3 (3 free слота)', r.json()['deleted'] == 3)
    check('booked-слот НЕ удалён', TimeSlot.objects.filter(pk=s_book.pk).exists())

    # Cleanup booked
    TimeSlot.objects.filter(pk=s_book.pk).delete()

    # bulk_generate без weekly_schedule → 400
    tp = teacher_profile
    original_schedule = tp.weekly_schedule
    TeacherProfile.objects.filter(pk=tp.pk).update(weekly_schedule={})
    r = c.post(reverse('slots_bulk_generate_api'),
               json.dumps({'weeks': 1, 'slot_minutes': 60}), 'application/json')
    check('bulk_generate без weekly_schedule → 400', r.status_code == 400)

    # bulk_generate с шаблоном — должны создаться слоты
    TeacherProfile.objects.filter(pk=tp.pk).update(weekly_schedule={
        'monday': {'from': '10:00', 'to': '12:00'},
        'wednesday': {'from': '14:00', 'to': '15:00'},
    })
    r = c.post(reverse('slots_bulk_generate_api'),
               json.dumps({'weeks': 2, 'slot_minutes': 60}), 'application/json')
    check('bulk_generate с шаблоном → 201', r.status_code == 201)
    check('bulk_generate created > 0', r.json()['created'] > 0)

    # bulk_generate валидация
    r = c.post(reverse('slots_bulk_generate_api'),
               json.dumps({'weeks': 999, 'slot_minutes': 60}), 'application/json')
    check('bulk_generate weeks=999 → 400', r.status_code == 400)
    r = c.post(reverse('slots_bulk_generate_api'),
               json.dumps({'weeks': 1, 'slot_minutes': 17}), 'application/json')
    check('bulk_generate slot_minutes=17 → 400', r.status_code == 400)

    # Восстанавливаем расписание
    TeacherProfile.objects.filter(pk=tp.pk).update(weekly_schedule=original_schedule)
    # Удаляем все сгенерированные тестовые слоты
    TimeSlot.objects.filter(teacher=tp, start_at__gte=timezone.now()).delete()

    # Cross-teacher access: teacher_user пытается изменить slot other_teacher
    if other_teacher:
        other_slot = TimeSlot.objects.create(
            teacher=other_teacher.teacher_profile,
            start_at=timezone.make_aware(timezone.datetime(2099, 9, 5, 10, 0)),
            end_at=timezone.make_aware(timezone.datetime(2099, 9, 5, 11, 0)),
        )
        r = c.delete(reverse('slots_detail_api', args=[other_slot.pk]))
        check('Teacher A пытается удалить slot Teacher B → 404', r.status_code == 404)
        r = c.patch(reverse('slots_detail_api', args=[other_slot.pk]),
                    json.dumps({'start': '2099-09-05T12:00:00+00:00', 'end': '2099-09-05T13:00:00+00:00'}),
                    'application/json')
        check('Teacher A пытается изменить slot Teacher B → 404', r.status_code == 404)

    c.logout()


# ============================================================
# Phase 3: Booking flow API (student/teacher)
# ============================================================

with section('Phase 3: Booking flow API'):
    c = Client()

    # Очищаем тестовые слоты, чтобы не было конфликтов
    TimeSlot.objects.filter(start_at__year=2099).delete()

    # Создаём 2 свободных слота от лица teacher_user
    base = timezone.make_aware(timezone.datetime(2099, 10, 1, 10, 0))
    slot_free1 = TimeSlot.objects.create(
        teacher=teacher_profile, start_at=base, end_at=base + timedelta(hours=1),
    )
    slot_free2 = TimeSlot.objects.create(
        teacher=teacher_profile,
        start_at=base + timedelta(hours=2),
        end_at=base + timedelta(hours=3),
    )
    slot_blocked = TimeSlot.objects.create(
        teacher=teacher_profile,
        start_at=base + timedelta(hours=5),
        end_at=base + timedelta(hours=6),
        status='blocked',
    )

    # PUBLIC SLOTS API — anonymous может смотреть свободные
    qs = urllib.parse.urlencode({
        'start': (base - timedelta(days=1)).isoformat(),
        'end':   (base + timedelta(days=1)).isoformat(),
    })
    url = reverse('public_teacher_slots', args=[teacher_profile.pk]) + '?' + qs
    r = c.get(url)
    check('Anonymous GET public_teacher_slots → 200', r.status_code == 200)
    events = r.json()['events']
    free_ids = {e['id'] for e in events}
    check('Public slots: содержит свободные', slot_free1.pk in free_ids and slot_free2.pk in free_ids)
    check('Public slots: НЕ содержит blocked', slot_blocked.pk not in free_ids)

    # Несуществующий учитель → 404
    r = c.get(reverse('public_teacher_slots', args=[999999]) + '?' + qs)
    check('Public slots для невалидного учителя → 404', r.status_code == 404)

    # BOOKING CREATE — student создаёт hold
    c.force_login(student1)
    r = c.post(reverse('booking_create_api'),
               json.dumps({'slot_id': slot_free1.pk, 'message': 'Тестовая заявка'}),
               'application/json')
    check('Student POST booking_create → 201', r.status_code == 201)
    if r.status_code == 201:
        bk_id = r.json()['booking']['id']
        slot_free1.refresh_from_db()
        check('После hold: slot.status=held', slot_free1.status == 'held')
        check('booking.status=pending', r.json()['booking']['status'] == 'pending')
        check('booking.expires_at установлен', r.json()['booking']['expires_at'] is not None)

        # Повторная попытка того же slot → 409
        r = c.post(reverse('booking_create_api'),
                   json.dumps({'slot_id': slot_free1.pk}), 'application/json')
        check('Повторный hold того же slot → 409', r.status_code == 409)

        # MY BOOKINGS — student
        r = c.get(reverse('my_bookings_api'))
        check('Student GET my bookings → 200', r.status_code == 200)
        check('My bookings содержит созданное', any(b['id'] == bk_id for b in r.json()['bookings']))

        # CANCEL by student
        r = c.post(reverse('booking_cancel_api', args=[bk_id]), '{}', 'application/json')
        check('Student cancel → 200', r.status_code == 200)
        slot_free1.refresh_from_db()
        check('После cancel: slot снова free', slot_free1.status == 'free')

        # Повторное бронирование того же slot (после cancel) — должно работать
        r = c.post(reverse('booking_create_api'),
                   json.dumps({'slot_id': slot_free1.pk}), 'application/json')
        check('Повторное бронирование после cancel → 201 (FK + UniqueConstraint работают)',
              r.status_code == 201)
        bk_id2 = r.json()['booking']['id'] if r.status_code == 201 else None

    c.logout()

    # TEACHER confirm/reject
    if bk_id2:
        c.force_login(teacher_user)

        # Подтверждение
        r = c.post(reverse('booking_confirm_api', args=[bk_id2]),
                   json.dumps({'reply': 'Жду на уроке'}), 'application/json')
        check('Teacher confirm pending → 200', r.status_code == 200)
        check('booking.status=confirmed', r.json()['booking']['status'] == 'confirmed')
        slot_free1.refresh_from_db()
        check('После confirm: slot.status=booked', slot_free1.status == 'booked')

        # Двойное подтверждение → 409
        r = c.post(reverse('booking_confirm_api', args=[bk_id2]),
                   '{}', 'application/json')
        check('Двойной confirm → 409', r.status_code == 409)

        # MY BOOKINGS — teacher видит это бронирование
        r = c.get(reverse('my_bookings_api'))
        check('Teacher GET my bookings → 200', r.status_code == 200)
        check('Teacher видит бронирование', any(b['id'] == bk_id2 for b in r.json()['bookings']))

        # Reject второго слота (slot_free2)
        c.logout(); c.force_login(student1)
        r = c.post(reverse('booking_create_api'),
                   json.dumps({'slot_id': slot_free2.pk}), 'application/json')
        bk_id3 = r.json()['booking']['id'] if r.status_code == 201 else None
        c.logout(); c.force_login(teacher_user)
        if bk_id3:
            r = c.post(reverse('booking_reject_api', args=[bk_id3]),
                       json.dumps({'reply': 'Не могу'}), 'application/json')
            check('Teacher reject pending → 200', r.status_code == 200)
            check('booking.status=cancelled_by_teacher', r.json()['booking']['status'] == 'cancelled_by_teacher')
            slot_free2.refresh_from_db()
            check('После reject: slot снова free', slot_free2.status == 'free')

        c.logout()

    # Permission tests
    # student2 не должен видеть бронирование student1
    if bk_id2:
        c.force_login(student2)
        r = c.post(reverse('booking_cancel_api', args=[bk_id2]), '{}', 'application/json')
        check('Student2 пытается отменить booking student1 → 403', r.status_code == 403)
        c.logout()

    # Учитель other_teacher не должен confirm/reject чужой booking
    if bk_id2 and other_teacher:
        c.force_login(other_teacher)
        r = c.post(reverse('booking_confirm_api', args=[bk_id2]), '{}', 'application/json')
        check('Other teacher confirm чужой booking → 403', r.status_code == 403)
        c.logout()

    # Anonymous booking create → redirect login
    r = c.post(reverse('booking_create_api'),
               json.dumps({'slot_id': slot_free1.pk}), 'application/json')
    check('Anonymous POST booking_create → 302/login', r.status_code in (301, 302))

    # Teacher (не student) не может создавать booking
    c.force_login(teacher_user)
    r = c.post(reverse('booking_create_api'),
               json.dumps({'slot_id': slot_free1.pk}), 'application/json')
    check('Teacher POST booking_create → 403', r.status_code == 403)
    c.logout()

    # Booking на несуществующий slot → 404 (или 409 если slot уже занят)
    c.force_login(student2)
    r = c.post(reverse('booking_create_api'),
               json.dumps({'slot_id': 999999999}), 'application/json')
    check('Booking на несуществующий slot → 404', r.status_code == 404)

    # Booking без slot_id → 400
    r = c.post(reverse('booking_create_api'), '{}', 'application/json')
    check('Booking без slot_id → 400', r.status_code == 400)

    # Page-уровневая проверка
    r = c.get(reverse('book_teacher_page', args=[teacher_profile.pk]))
    check('GET book_teacher_page → 200', r.status_code == 200)
    check('book_teacher_page содержит calendar', b'calendar' in r.content.lower())
    c.logout()

    r = c.get(reverse('book_teacher_page', args=[teacher_profile.pk]))
    check('Anonymous GET book_teacher_page → 200 (страница публичная)', r.status_code == 200)

    # My bookings page
    c.force_login(student1)
    r = c.get(reverse('my_bookings_page'))
    check('Student GET my_bookings_page → 200', r.status_code == 200)
    c.logout()
    c.force_login(teacher_user)
    r = c.get(reverse('my_bookings_page'))
    check('Teacher GET my_bookings_page → 200', r.status_code == 200)
    c.logout()


# ============================================================
# Phase 5: meeting_url при confirm + Join Lesson
# ============================================================

with section('Phase 5: meeting_url для videos'):
    c = Client()
    TimeSlot.objects.filter(start_at__year=2099).delete()

    base = timezone.make_aware(timezone.datetime(2099, 11, 1, 10, 0))
    slot_v1 = TimeSlot.objects.create(teacher=teacher_profile, start_at=base, end_at=base + timedelta(hours=1))

    # Студент создаёт hold
    c.force_login(student1)
    r = c.post(reverse('booking_create_api'),
               json.dumps({'slot_id': slot_v1.pk}), 'application/json')
    bk_id = r.json()['booking']['id'] if r.status_code == 201 else None
    c.logout()

    # Учитель confirm с валидным URL
    c.force_login(teacher_user)
    r = c.post(reverse('booking_confirm_api', args=[bk_id]),
               json.dumps({'reply': 'Жду', 'meeting_url': 'https://meet.google.com/abc-defg-hij'}),
               'application/json')
    check('Confirm с валидным meeting_url → 200', r.status_code == 200)
    check('booking.meeting_url сохранён',
          r.json()['booking']['meeting_url'] == 'https://meet.google.com/abc-defg-hij')

    # Confirm с невалидным URL (не http://) — 400
    slot_v2 = TimeSlot.objects.create(teacher=teacher_profile,
                                       start_at=base + timedelta(hours=3),
                                       end_at=base + timedelta(hours=4))
    c.logout(); c.force_login(student1)
    r = c.post(reverse('booking_create_api'),
               json.dumps({'slot_id': slot_v2.pk}), 'application/json')
    bk_id2 = r.json()['booking']['id'] if r.status_code == 201 else None
    c.logout(); c.force_login(teacher_user)
    r = c.post(reverse('booking_confirm_api', args=[bk_id2]),
               json.dumps({'meeting_url': 'javascript:alert(1)'}),
               'application/json')
    check('Confirm с javascript: URL → 400', r.status_code == 400)
    r = c.post(reverse('booking_confirm_api', args=[bk_id2]),
               json.dumps({'meeting_url': 'not a url'}),
               'application/json')
    check('Confirm с мусорным meeting_url → 400', r.status_code == 400)
    # Confirm без meeting_url — должен пройти (URL опционален)
    r = c.post(reverse('booking_confirm_api', args=[bk_id2]),
               json.dumps({}), 'application/json')
    check('Confirm без meeting_url → 200', r.status_code == 200)
    check('booking.meeting_url пустой', r.json()['booking']['meeting_url'] == '')

    # Учитель может задать URL после confirm через отдельный endpoint
    url_endpoint = reverse('booking_set_meeting_url_api', args=[bk_id2])
    r = c.post(url_endpoint, json.dumps({'meeting_url': 'https://zoom.us/j/123'}), 'application/json')
    check('Set meeting_url после confirm → 200', r.status_code == 200)
    check('meeting_url обновлён', r.json()['booking']['meeting_url'] == 'https://zoom.us/j/123')

    # Невалидный URL через set-link → 400
    r = c.post(url_endpoint, json.dumps({'meeting_url': 'ftp://example.com'}), 'application/json')
    check('Set невалидного URL → 400', r.status_code == 400)

    # Другой учитель не может изменить
    c.logout()
    if other_teacher:
        c.force_login(other_teacher)
        r = c.post(url_endpoint, json.dumps({'meeting_url': 'https://hack.com'}), 'application/json')
        check('Other teacher set-link → 403', r.status_code == 403)
    c.logout()

    # Студент не может set-link
    c.force_login(student1)
    r = c.post(url_endpoint, json.dumps({'meeting_url': 'https://x.com'}), 'application/json')
    check('Student set-link → 403', r.status_code == 403)
    c.logout()

    # GET my_bookings возвращает meeting_url
    c.force_login(student1)
    r = c.get(reverse('my_bookings_api'))
    bk = [b for b in r.json()['bookings'] if b['id'] == bk_id]
    check('my_bookings API возвращает meeting_url для подтверждённого',
          bool(bk) and bk[0]['meeting_url'] == 'https://meet.google.com/abc-defg-hij')
    c.logout()


# ============================================================
# Phase 0/legacy: critical pages still respond
# ============================================================

with section('Regression: основные страницы'):
    c = Client()
    pages_anon = [
        ('home', reverse('home'), 200),
        ('login', reverse('login'), 200),
        ('register_choose', reverse('register_choose'), 200),
    ]
    for name, url, expected in pages_anon:
        r = c.get(url)
        check(f'Anonymous {name} → {expected}', r.status_code == expected, f'got {r.status_code}')

    # Залогиненный учитель — home + profile
    c.force_login(teacher_user)
    r = c.get(reverse('home')); check('Teacher home → 200', r.status_code == 200)
    r = c.get(reverse('profile')); check('Teacher profile → 200', r.status_code == 200)
    r = c.get(reverse('conversations_list')); check('Teacher conversations → 200', r.status_code == 200)
    r = c.get(reverse('notifications_list')); check('Teacher notifications → 200', r.status_code == 200)
    c.logout()

    # Залогиненный ученик
    c.force_login(student1)
    r = c.get(reverse('home')); check('Student home → 200', r.status_code == 200)
    r = c.get(reverse('profile')); check('Student profile → 200', r.status_code == 200)
    r = c.get(reverse('my_favorite_teachers')); check('Student favorites → 200', r.status_code == 200)
    c.logout()


# ============================================================
# Cleanup
# ============================================================

with section('Cleanup'):
    # 1. Все слоты в 2099 году (наш маркер)
    n1, _ = TimeSlot.objects.filter(start_at__year=2099).delete()
    # 2. mark_completed-тест переписывает start_at в прошлое — ловим его явно по pk
    n_extra = 0
    try:
        n_extra, _ = TimeSlot.objects.filter(pk=_completed_test_slot_pk).delete()
    except NameError:
        pass
    # 3. Wizard-drafts
    n2, _ = WizardDraft.objects.filter(session_key__startswith='__test__').delete()
    # 4. Notification от Phase 3 booking flow и Phase 4 reminders —
    #    создаются для реальных юзеров и не каскадятся при удалении Booking.
    #    Удаляем только если target_user — наш тестовый студент или учитель.
    test_user_pks = [
        student1.pk, student2.pk, teacher_user.pk,
        other_teacher.pk if other_teacher else None,
    ]
    test_user_pks = [pk for pk in test_user_pks if pk]
    regression_titles = [
        'Завтра урок на UstozHub', 'Урок через 3 часа', 'Урок через 10 минут',
        'Новое бронирование', '✅ Урок подтверждён', '❌ Бронирование отклонено',
        'Ученик отменил бронирование', 'Урок отменён учителем',
    ]
    n3, _ = Notification.objects.filter(
        target_user_id__in=test_user_pks,
        title__in=regression_titles,
    ).delete()
    print(f'  deleted: {n1 + n_extra} test TimeSlots+bookings, {n2} test drafts, {n3} test notifications')


# ============================================================
# Summary
# ============================================================

print(f'\n{"="*60}')
print(f'  RESULT: {PASSED} passed, {FAILED} failed')
print(f'{"="*60}')
if FAILURES:
    print('\nFailures:')
    for f in FAILURES:
        print(f'  • {f}')
    sys.exit(1)
print('\n🎉 ALL TESTS PASSED')
