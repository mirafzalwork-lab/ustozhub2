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


@shared_task(name='teachers.broadcast_notification_push', bind=True, max_retries=2)
def broadcast_notification_push(self, notification_id: int) -> int:
    """
    Рассылает WebSocket-пуш по группе пользователей (all/students/teachers/admins).

    Вынесено из синхронного post_save сигнала, чтобы не блокировать
    web-worker при массовых уведомлениях (10k+ пользователей).
    Возвращает количество получателей.
    """
    from .models import Notification, User
    from .consumers import notify_user
    from .context_processors import invalidate_notification_cache

    try:
        n = Notification.objects.get(pk=notification_id, is_active=True)
    except Notification.DoesNotExist:
        logger.warning(f'broadcast_notification_push: notification {notification_id} not found')
        return 0

    payload = {'id': n.id, 'title': n.title, 'short_text': n.short_text}

    qs = User.objects.filter(is_active=True)
    if n.target == 'students':
        qs = qs.filter(user_type='student')
    elif n.target == 'teachers':
        qs = qs.filter(user_type='teacher')
    elif n.target == 'admins':
        qs = qs.filter(is_staff=True)
    elif n.target != 'all':
        return 0  # неожиданный target — пропускаем

    sent = 0
    # Батчим по 500 — баланс между памятью и числом round-trips к channel layer
    for uid in qs.values_list('id', flat=True).iterator(chunk_size=500):
        try:
            invalidate_notification_cache(uid)
            notify_user(uid, 'new_notification', payload)
            sent += 1
        except Exception as e:
            logger.warning(f'broadcast push failed for user_id={uid}: {e}')

    logger.info(f'broadcast_notification_push: notification={n.id} target={n.target} sent={sent}')
    return sent


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
    from django.utils import translation, timezone
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

            # Не рассылаем сырую ссылку на видео-комнату (её мог бы открыть кто
            # угодно). Вместо неё — ссылка на нашу комнату урока, доступную только
            # участникам после аутентификации (см. lesson_room).
            try:
                room_url = f"{site_url}{reverse('lesson_room', args=[booking.id])}"
            except Exception:
                room_url = booking.meeting_url

            ctx = {
                'recipient_name': recipient_user.get_full_name() or recipient_user.username,
                'teacher_name': teacher_user.get_full_name() or teacher_user.username,
                'student_name': student_user.get_full_name() or student_user.username,
                'subject_name': booking.subject.name if booking.subject else '',
                # localtime: slot.start_at хранится в UTC (USE_TZ=True); без
                # перевода в Asia/Tashkent напоминание показывало бы время на 5ч раньше.
                'start_at': timezone.localtime(slot.start_at).strftime('%d.%m.%Y %H:%M'),
                'duration_minutes': slot.duration_minutes,
                'meeting_url': room_url,
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
                if room_url:
                    full += f'\n\nСсылка на урок: {room_url}'
                Notification.objects.create(
                    title=subject_line,
                    short_text=short,
                    full_text=full,
                    target='specific_user',
                    target_user=recipient_user,
                    priority=8,
                    is_active=True,
                    category=Notification.Category.REMINDER,
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
    from datetime import timedelta
    from django.utils import timezone
    from .models import Booking

    now = timezone.now()
    # Завершаем урок только после закрытия окна входа в комнату (end_at + 30 мин,
    # как в lesson_room). Иначе опоздавший/затянувшийся урок мог быть помечен
    # no_show_teacher до того, как учитель реально подключился.
    settle_cutoff = now - timedelta(minutes=30)
    to_settle = Booking.objects.filter(
        status='confirmed',
        slot__end_at__lt=settle_cutoff,
    ).select_related('slot')

    count = 0
    no_show = 0
    no_show_student = 0
    not_held = 0
    for booking in to_settle:
        try:
            result = booking.settle_after_end()
            if result == 'no_show_teacher':
                no_show += 1
                _refund_teacher_no_show(booking)
                _notify_teacher_no_show(booking)
            elif result == 'no_show_student':
                # Прощённая неявка — урок возвращается ученику (без выплаты);
                # засчитанная (4-я+) — выплата уйдёт через release_pending_payouts.
                no_show_student += 1
                _handle_student_no_show(booking)
            elif result == 'not_held':
                # ТЗ §8 — никто не пришёл: урок возвращается ученику.
                not_held += 1
                _handle_not_held(booking)
            elif result == 'completed':
                count += 1
        except Exception as e:
            logger.error(
                f'mark_completed_lessons: failed for booking {booking.pk}: {e}',
                exc_info=True,
            )

    if count or no_show or no_show_student or not_held:
        logger.info(
            f'mark_completed_lessons: completed {count}, teacher no-show {no_show}, '
            f'student no-show {no_show_student}, not held {not_held}'
        )
    return count


def _notify(user, *, title, short_text, full_text, category, booking=None,
            priority=5, action_url=''):
    """Создать персональное in-app уведомление (никогда не роняет поток)."""
    from .models import Notification
    try:
        Notification.objects.create(
            title=title, short_text=short_text[:300], full_text=full_text,
            target='specific_user', target_user=user, priority=priority,
            is_active=True, category=category, booking=booking,
            action_url=action_url or '',
        )
    except Exception:
        logger.warning('notify failed for user=%s', getattr(user, 'pk', None), exc_info=True)


def _safe_url(name, *args):
    from django.urls import reverse
    try:
        return reverse(name, args=args)
    except Exception:
        return ''


def _notify_teacher_no_show(booking) -> None:
    """ТЗ §7: учитель не пришёл — уведомляем ученика, предлагаем новую дату."""
    from .models import Notification
    teacher = booking.slot.teacher.user.get_full_name() or booking.slot.teacher.user.username
    _notify(
        booking.student,
        title='Преподаватель не подключился',
        short_text=f'Урок с {teacher} не состоялся по вине преподавателя.',
        full_text=(
            f'Преподаватель {teacher} не подключился к уроку. '
            f'Урок возвращён вам — выберите новую дату в расписании.'
        ),
        category=Notification.Category.LESSON,
        booking=booking, priority=7,
        action_url=_safe_url('my_bookings_page'),
    )


def _handle_not_held(booking) -> None:
    """ТЗ §8: никто не пришёл. Возврат денег (никто не виноват — не списываем):
    платный пробный → refund_trial; урок подписки → refund_lesson (возврат на
    кошелёк ученика + уменьшение пакета). Раньше для подписки эскроу зависал до
    закрытия подписки (недели) — деньги были заперты, если перебронировать некуда."""
    from .models import Notification
    if booking.is_trial and booking.trial_price_paid:
        try:
            from billing.services import TrialService
            TrialService.refund_trial(booking, reason='Урок не состоялся (никто не подключился)')
            from .models import LessonEvent
            LessonEvent.log(booking, 'refund', meta={'reason': 'not_held'})
        except Exception:
            logger.warning('not_held refund_trial failed booking=%s', booking.pk, exc_info=True)
    elif booking.subscription_id:
        try:
            from billing.services import SubscriptionService
            SubscriptionService.refund_lesson(
                booking, cancelled_by='teacher',
                reason='Урок не состоялся (никто не подключился)',
            )
            from .models import LessonEvent
            LessonEvent.log(booking, 'refund', meta={'reason': 'not_held'})
        except Exception:
            logger.warning('not_held refund_lesson failed booking=%s', booking.pk, exc_info=True)
    for user in (booking.student, booking.slot.teacher.user):
        _notify(
            user,
            title='Урок не состоялся',
            short_text='К уроку никто не подключился.',
            full_text=(
                'Урок не состоялся — к видеокомнате никто не подключился. '
                'Средства не списаны. Можно выбрать новую дату.'
            ),
            category=Notification.Category.LESSON,
            booking=booking, priority=6,
            action_url=_safe_url('my_bookings_page'),
        )


def _handle_student_no_show(booking) -> None:
    """ТЗ §6: неявка ученика. Эскалация предупреждений; с (N+1)-й — урок списан."""
    from django.conf import settings
    from .models import Booking, LessonEvent, Notification
    limit = getattr(settings, 'STUDENT_NO_SHOW_FORGIVE_LIMIT', 3)
    # Порядковый номер этой неявки за окно (текущая уже сохранена settle_after_end).
    ordinal = Booking.count_student_no_shows(booking.student_id)
    teacher = booking.slot.teacher.user.get_full_name() or booking.slot.teacher.user.username

    if booking.no_show_forgiven:
        # 1-я: информируем; 2-я: предупреждение; 3-я (== limit): последнее.
        if ordinal <= 1:
            title = 'Вы пропустили урок'
            warn = (
                f'Вы не подключились к уроку с {teacher}. В этот раз урок возвращён вам — '
                f'выберите новую дату. Пожалуйста, предупреждайте об отмене заранее.'
            )
        elif ordinal < limit:
            title = 'Повторный пропуск урока'
            warn = (
                f'Вы снова пропустили урок с {teacher}. Урок возвращён, но при '
                f'{limit + 1}-м пропуске за 90 дней урок будет списан без возврата.'
            )
        else:
            title = 'Последнее предупреждение'
            warn = (
                f'Это {ordinal}-й пропуск за 90 дней. Урок возвращён в последний раз: '
                f'следующая неявка приведёт к списанию урока и оплате преподавателю.'
            )
        priority = 7 if ordinal < limit else 9
    else:
        title = 'Урок списан из-за пропуска'
        warn = (
            f'Вы пропустили урок с {teacher} ({ordinal}-й раз за 90 дней). '
            f'Урок списан из вашего пакета, оплата начислена преподавателю.'
        )
        priority = 9

    _notify(
        booking.student, title=title, short_text=warn[:120], full_text=warn,
        category=Notification.Category.WARNING, booking=booking, priority=priority,
        action_url=_safe_url('my_bookings_page'),
    )
    LessonEvent.log(
        booking, 'warning_sent', actor='system',
        ordinal=ordinal, forgiven=booking.no_show_forgiven,
    )


def _refund_teacher_no_show(booking) -> None:
    """Учитель не пришёл на урок → возврат ученику (без выплаты учителю).

    Платный пробный → refund_trial; урок подписки → refund_lesson.
    Бесплатный пробный / разовый урок — возвращать нечего.
    """
    try:
        if booking.is_trial and booking.trial_price_paid:
            from billing.services import TrialService
            TrialService.refund_trial(booking, reason='Учитель не подключился к уроку')
        elif booking.subscription_id:
            from billing.services import SubscriptionService
            SubscriptionService.refund_lesson(
                booking, cancelled_by='teacher',
                reason='Учитель не подключился к уроку',
            )
    except Exception as e:
        logger.error(
            f'_refund_teacher_no_show failed for booking {booking.pk}: {e}',
            exc_info=True,
        )
