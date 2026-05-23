"""
Celery-задачи приложения teachers.

В Phase 0 — только skeleton с health-check.
В Phase 4 сюда добавятся:
    • send_lesson_reminder(lesson_id, hours_before)
    • release_expired_booking_holds()
    • send_email_notification(user_id, template, context)
    • daily_reminder_dispatch()
"""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='teachers.health_check')
def health_check() -> dict:
    """Простой ping для проверки что Celery worker жив и видит наши задачи."""
    from django.utils import timezone
    return {'ok': True, 'at': timezone.now().isoformat()}


@shared_task(name='teachers.cleanup_wizard_drafts_async')
def cleanup_wizard_drafts_async(days: int = 14) -> int:
    """Удалить устаревшие WizardDraft. Дублирует management-команду,
    но удобно для Celery Beat-расписания."""
    from datetime import timedelta
    from django.utils import timezone
    from .models import WizardDraft

    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = WizardDraft.objects.filter(updated_at__lt=cutoff).delete()
    logger.info(f'cleanup_wizard_drafts_async: deleted {deleted} drafts')
    return deleted


@shared_task(name='teachers.release_expired_holds')
def release_expired_holds() -> int:
    """
    Освобождает слоты с истёкшим 15-мин hold.
    Запускается Celery Beat каждую минуту.

    Логика: для каждого Booking со status='pending' и expires_at<now
    переводим в 'expired', а связанный TimeSlot — в 'free'.
    """
    from django.utils import timezone
    from .models import Booking

    now = timezone.now()
    expired = Booking.objects.filter(
        status='pending',
        expires_at__lt=now,
    ).select_related('slot')

    count = 0
    for booking in expired:
        try:
            booking.expire()
            count += 1
        except Exception as e:
            logger.error(
                f'release_expired_holds: failed to expire booking {booking.pk}: {e}',
                exc_info=True,
            )

    if count:
        logger.info(f'release_expired_holds: expired {count} bookings')
    return count


@shared_task(name='teachers.send_lesson_reminders')
def send_lesson_reminders() -> int:
    """
    Раз в минуту через Celery Beat. Находит confirmed бронирования,
    у которых start_at попадает в одно из окон T-{24h, 3h, 10min} с допуском 90 сек,
    и отправляет напоминание (email + in-app Notification).

    Дедупликация: LessonReminderSent(booking, kind) UNIQUE — повторно не отправит.

    Возвращает количество отправленных напоминаний.
    """
    from datetime import timedelta
    from django.utils import timezone
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.urls import reverse
    from django.db import IntegrityError
    from .models import Booking, LessonReminderSent, Notification

    now = timezone.now()
    # Допуск — окно ±90 сек (Beat запускается каждые 60c)
    tolerance = timedelta(seconds=90)
    windows = [
        ('24h', timedelta(hours=24)),
        ('3h', timedelta(hours=3)),
        ('10min', timedelta(minutes=10)),
    ]

    from django.db import transaction
    sent_total = 0
    for kind, delta in windows:
        target_time = now + delta
        # Ищем бронирования, у которых slot.start_at в окне target_time ± tolerance
        qs = Booking.objects.filter(
            status='confirmed',
            slot__start_at__gte=target_time - tolerance,
            slot__start_at__lte=target_time + tolerance,
        ).select_related('slot', 'slot__teacher__user', 'student', 'subject')

        for booking in qs:
            # Каждая попытка — в savepoint, чтобы IntegrityError (idempotency)
            # не рушил outer-транзакцию вызывающего кода.
            try:
                with transaction.atomic():
                    # Сначала пробуем создать запись — если упадёт на UNIQUE,
                    # значит уже отправлено, и savepoint откатится без побочек.
                    LessonReminderSent.objects.create(
                        booking=booking, kind=kind, channels='',
                    )
            except IntegrityError:
                continue  # уже отправлено в этой kind-окно

            # Запись создана — теперь отправляем (не в savepoint, чтобы
            # WS push реально дошёл, а не откатился). Channels обновим после.
            try:
                channels = _send_reminder_for_booking(booking, kind)
                LessonReminderSent.objects.filter(
                    booking=booking, kind=kind,
                ).update(channels=','.join(channels))
                sent_total += 1
            except Exception as e:
                logger.error(
                    f'send_lesson_reminders: failed for booking={booking.pk} kind={kind}: {e}',
                    exc_info=True,
                )

    if sent_total:
        logger.info(f'send_lesson_reminders: sent {sent_total} reminders')
    return sent_total


def _send_reminder_for_booking(booking, kind):
    """
    Отправляет email + создаёт Notification (in-app + WS) учителю и ученику.
    Возвращает список каналов, через которые отправили: ['email', 'in_app'].
    """
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.urls import reverse
    from django.utils import translation
    from .models import Notification

    slot = booking.slot
    teacher_user = slot.teacher.user
    student_user = booking.student
    site_url = getattr(settings, 'SITE_URL', 'https://ustozhubedu.uz')

    # Локализованный URL: reverse возвращает с префиксом текущей activate'нной локали.
    # Для простоты — без префикса в reverse, для каждого получателя активируем язык.
    channels_used = []

    for recipient_user, role in [(teacher_user, 'teacher'), (student_user, 'student')]:
        # Активируем локаль получателя (по умолчанию ru)
        with translation.override('ru'):
            try:
                bookings_url = reverse('my_bookings_page')
            except Exception:
                bookings_url = '/my/bookings/'

            ctx = {
                'recipient_name': recipient_user.get_full_name() or recipient_user.username,
                'teacher_name': teacher_user.get_full_name() or teacher_user.username,
                'student_name': student_user.get_full_name() or student_user.username,
                'subject_name': booking.subject.name if booking.subject else '',
                'start_at': slot.start_at.strftime('%d.%m.%Y %H:%M'),
                'duration_minutes': slot.duration_minutes,
                'meeting_url': booking.meeting_url,
                'kind': kind,
                'site_url': site_url,
                'bookings_url': bookings_url,
            }
            subject_line = {
                '24h': 'Завтра урок на UstozHub',
                '3h': 'Урок через 3 часа',
                '10min': 'Урок через 10 минут',
            }[kind]
            text_body = render_to_string('emails/lesson_reminder.txt', ctx)
            html_body = render_to_string('emails/lesson_reminder.html', ctx)

            # Email
            if recipient_user.email:
                try:
                    msg = EmailMultiAlternatives(
                        subject=subject_line,
                        body=text_body,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[recipient_user.email],
                    )
                    msg.attach_alternative(html_body, 'text/html')
                    msg.send(fail_silently=False)
                    channels_used.append(f'email:{role}')
                except Exception as e:
                    logger.warning(f'reminder email failed for {recipient_user.email}: {e}')

            # In-app Notification (signal автоматически WS push на клиента)
            try:
                short = subject_line
                full = f'{short}.\n\nУчитель: {teacher_user.get_full_name() or teacher_user.username}\n' \
                       f'Ученик: {student_user.get_full_name() or student_user.username}\n' \
                       f'Когда: {ctx["start_at"]}'
                if booking.meeting_url:
                    full += f'\n\nСсылка: {booking.meeting_url}'
                Notification.objects.create(
                    title=subject_line,
                    short_text=short,
                    full_text=full,
                    target='specific_user',
                    target_user=recipient_user,
                    priority=8,
                    is_active=True,
                )
                channels_used.append(f'in_app:{role}')
            except Exception as e:
                logger.warning(f'reminder in-app failed for {recipient_user.pk}: {e}')

            # Telegram (если пользователь привязал аккаунт и не отключил уведомления)
            try:
                if _send_telegram_reminder(recipient_user, subject_line, ctx):
                    channels_used.append(f'telegram:{role}')
            except Exception as e:
                logger.warning(f'reminder telegram failed for {recipient_user.pk}: {e}')

    return channels_used


def _send_telegram_reminder(recipient_user, subject_line, ctx) -> bool:
    """Шлёт напоминание в Telegram привязанному пользователю. Возвращает True при успехе.

    Не зависит от настройки SMTP — нужен только запущенный бот и привязка.
    """
    from html import escape
    from .models import TelegramUser
    from .admin_telegram_service import admin_telegram_service

    tg = TelegramUser.objects.filter(
        user=recipient_user, started_bot=True, notifications_enabled=True,
    ).first()
    if not tg:
        return False

    lines = [f'⏰ <b>{escape(subject_line)}</b>', '']
    if ctx.get('subject_name'):
        lines.append(f'📚 {escape(ctx["subject_name"])}')
    lines.append(f'👤 {escape(ctx["teacher_name"])} ↔ {escape(ctx["student_name"])}')
    lines.append(f'🕒 {escape(ctx["start_at"])} ({ctx["duration_minutes"]} мин)')
    if ctx.get('meeting_url'):
        lines.append(f'🎥 {escape(ctx["meeting_url"])}')
    lines.append('')
    lines.append(f'{escape(ctx["site_url"])}{ctx["bookings_url"]}')
    text = '\n'.join(lines)

    return admin_telegram_service.send_message_simple(
        telegram_id=tg.telegram_id, text=text, parse_mode='HTML',
    )


@shared_task(name='teachers.mark_completed_lessons')
def mark_completed_lessons() -> int:
    """
    Помечает confirmed-бронирования как completed после end_at слота.
    Запускается Celery Beat каждые 5 минут.
    """
    from django.utils import timezone
    from .models import Booking

    now = timezone.now()
    to_complete = Booking.objects.filter(
        status='confirmed',
        slot__end_at__lt=now,
    ).select_related('slot')

    count = 0
    for booking in to_complete:
        try:
            booking.mark_completed()
            count += 1
        except Exception as e:
            logger.error(
                f'mark_completed_lessons: failed for booking {booking.pk}: {e}',
                exc_info=True,
            )

    if count:
        logger.info(f'mark_completed_lessons: completed {count} lessons')
    return count
