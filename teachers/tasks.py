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


def email_delivery_configured() -> bool:
    """True, если на сервере реально настроен SMTP для отправки писем.

    Прод может работать с EMAIL_BACKEND=smtp, но пустым EMAIL_HOST — тогда
    smtplib.SMTP('', port) не подключается и любая отправка падает с
    «please run connect() first». В этом случае email просто выключен: письма
    не шлём (in-app + Telegram-напоминания продолжают работать), а не сыпем
    ошибками и ретраями на каждое уведомление. Console/file/dummy-бэкенды не
    требуют хоста и считаются настроенными.
    """
    from django.conf import settings
    backend = getattr(settings, 'EMAIL_BACKEND', '')
    if 'smtp' not in backend:
        return True
    return bool(getattr(settings, 'EMAIL_HOST', ''))


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
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer

    from .models import Notification

    try:
        n = Notification.objects.get(pk=notification_id, is_active=True)
    except Notification.DoesNotExist:
        logger.warning(f'broadcast_notification_push: notification {notification_id} not found')
        return 0

    payload = {'id': n.id, 'title': n.title, 'short_text': n.short_text}

    # Аудит 2026-06-10 H15: ОДИН group_send в общую broadcast-группу
    # (NotificationConsumer подписывает сокет на broadcast_<target> при
    # connect) вместо цикла по всем N пользователям с per-user group_send +
    # cache.delete (на 100k пользователей задача убивалась по time limit).
    # Per-user инвалидация бейджей не нужна: кэш бейджа живёт 30с и догонит
    # сам, а тост приходит мгновенно по WS.
    group_by_target = {
        'all': 'broadcast_all',
        'students': 'broadcast_students',
        'teachers': 'broadcast_teachers',
        'admins': 'broadcast_admins',
    }
    group = group_by_target.get(n.target)
    if group is None:
        return 0  # неожиданный target — пропускаем

    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.warning('broadcast_notification_push: channel layer недоступен')
        return 0
    async_to_sync(channel_layer.group_send)(group, {
        'type': 'push_notification',
        'event_type': 'new_notification',
        'payload': payload,
    })

    logger.info(f'broadcast_notification_push: notification={n.id} target={n.target} group={group}')
    return 1


@shared_task(
    name='teachers.send_notification_email',
    bind=True, max_retries=3, default_retry_delay=60,
)
def send_notification_email(self, notification_id: int) -> bool:
    """Дублирует персональное уведомление на email пользователя.

    Вызывается из сигнала push_notification_realtime для target='specific_user'.
    Идемпотентность не нужна (одно уведомление = одно письмо), но при сбое SMTP
    задача ретраится. Не отправляет, если у пользователя нет email, отключён
    приём писем или фича выключена флагом NOTIFY_EMAIL_ENABLED.

    Возвращает True, если письмо ушло (передано backend'у).
    """
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string
    from django.utils.html import strip_tags
    from .models import Notification

    if not getattr(settings, 'NOTIFY_EMAIL_ENABLED', True):
        return False
    if not email_delivery_configured():
        # SMTP не настроен (пустой EMAIL_HOST) — не уходим в ретраи с
        # «please run connect() first». Уведомление уже доставлено in-app и
        # (при наличии) в Telegram.
        return False

    try:
        n = Notification.objects.select_related('target_user').get(
            pk=notification_id, is_active=True,
        )
    except Notification.DoesNotExist:
        logger.warning('send_notification_email: notification %s not found', notification_id)
        return False

    user = n.target_user
    if not user or not user.email or not user.is_active:
        return False
    # Уважение opt-out: поле на профиле может появиться позже — по умолчанию шлём.
    if getattr(user, 'email_notifications', True) is False:
        return False

    base = getattr(settings, 'SITE_BASE_URL', '').rstrip('/')
    action_url = n.action_url or ''
    if action_url.startswith('/'):
        action_url = f'{base}{action_url}'

    context = {
        'title': n.title,
        'full_text': n.full_text or n.short_text,
        'action_url': action_url,
        'site_url': base,
        'user': user,
    }
    try:
        html_body = render_to_string('email/notification.html', context)
        text_body = strip_tags(n.full_text or n.short_text)
        if action_url:
            text_body = f'{text_body}\n\n{action_url}'

        msg = EmailMultiAlternatives(
            subject=n.title,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        msg.attach_alternative(html_body, 'text/html')
        msg.send(fail_silently=False)
        logger.info('send_notification_email: sent notification=%s to %s', n.id, user.email)
        return True
    except Exception as exc:
        logger.warning(
            'send_notification_email: failed notification=%s to=%s: %s',
            n.id, user.email, exc,
        )
        raise self.retry(exc=exc)


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

            # Email (только если SMTP реально настроен — иначе не тратим
            # попытку и не засоряем лог «please run connect() first»).
            if recipient_user.email and email_delivery_configured():
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

            # In-app Notification (signal автоматически делает WS push + email +
            # Telegram через мост push_notification_realtime). action_url=room_url
            # превращается в Telegram в кнопку «🎥 Войти в урок».
            try:
                subj_part = f' · {ctx["subject_name"]}' if ctx.get('subject_name') else ''
                short = f'{subject_line} — {ctx["start_at"]}{subj_part}'
                full = f'{subject_line}.\n\nУчитель: {teacher_user.get_full_name() or teacher_user.username}\n' \
                       f'Ученик: {student_user.get_full_name() or student_user.username}\n' \
                       f'Когда: {ctx["start_at"]} ({slot.duration_minutes} мин)'
                if room_url:
                    full += f'\n\nСсылка на урок: {room_url}'
                Notification.objects.create(
                    title=subject_line,
                    short_text=short[:300],
                    full_text=full,
                    target='specific_user',
                    target_user=recipient_user,
                    priority=8,
                    is_active=True,
                    category=Notification.Category.REMINDER,
                    action_url=room_url or '',
                )
                channels_used.append(f'in_app:{role}')
            except Exception as e:
                logger.warning(f'reminder in-app failed for {recipient_user.pk}: {e}')

    return channels_used


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


def _booking_has_deposit(booking) -> bool:
    """True, если у разовой брони есть депозит (billing.BookingDeposit)."""
    try:
        from billing.models import BookingDeposit
        return BookingDeposit.objects.filter(booking_id=booking.pk).exists()
    except Exception:
        return False


def _notify_teacher_no_show(booking) -> None:
    """ТЗ §7: учитель не пришёл — уведомляем ученика.

    Текст соответствует фактическому движению денег (аудит 2026-06-10 M15):
    _refund_teacher_no_show возвращает стоимость урока НА БАЛАНС (и уменьшает
    пакет подписки) — раньше текст обещал «урок возвращён, выберите новую
    дату», и ученик искал несуществующий урок в пакете.
    """
    from .models import Notification
    teacher = booking.slot.teacher.user.get_full_name() or booking.slot.teacher.user.username
    if booking.subscription_id or (booking.is_trial and booking.trial_price_paid):
        money_text = (
            'Стоимость урока возвращена на ваш баланс — её можно потратить '
            'на нового учителя или новую подписку.'
        )
    elif _booking_has_deposit(booking):
        money_text = 'Депозит за урок возвращён на ваш баланс.'
    else:
        money_text = 'Деньги за урок не списывались.'
    _notify(
        booking.student,
        title='Преподаватель не подключился',
        short_text=f'Урок с {teacher} не состоялся по вине преподавателя.',
        full_text=(
            f'Преподаватель {teacher} не подключился к уроку. {money_text}'
        ),
        category=Notification.Category.LESSON,
        booking=booking, priority=7,
        action_url=_safe_url('my_bookings_page'),
    )

    # Учителю — уведомление о собственной неявке и возврате средств ученику.
    if booking.subscription_id or (booking.is_trial and booking.trial_price_paid):
        teacher_money_text = 'Стоимость урока возвращена ученику.'
    elif _booking_has_deposit(booking):
        teacher_money_text = 'Депозит за урок возвращён ученику.'
    else:
        teacher_money_text = 'Деньги за урок с ученика не списывались.'
    _notify(
        booking.slot.teacher.user,
        title='Вы не подключились к уроку',
        short_text='Урок не состоялся: вы не подключились к видеокомнате.',
        full_text=(
            f'Вы не подключились к уроку. Урок не состоялся по вине '
            f'преподавателя, оплата вам не начисляется. {teacher_money_text}'
        ),
        category=Notification.Category.WARNING,
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
    else:
        # Разовый урок с депозитом: никто не пришёл — ничья вина, депозит возвращаем.
        try:
            from billing.deposits import DepositService
            if DepositService.refund(booking, reason='Урок не состоялся (никто не подключился)'):
                from .models import LessonEvent
                LessonEvent.log(booking, 'refund', meta={'reason': 'not_held'})
        except Exception:
            logger.warning('not_held deposit refund failed booking=%s', booking.pk, exc_info=True)
    # Текст соответствует фактическому движению денег (аудит 2026-06-10 M15):
    # выше refund_trial/refund_lesson вернули стоимость урока на баланс ученика.
    if booking.subscription_id or (booking.is_trial and booking.trial_price_paid):
        money_text = 'Стоимость урока возвращена на баланс ученика.'
    elif _booking_has_deposit(booking):
        money_text = 'Депозит возвращён на баланс ученика.'
    else:
        money_text = 'Деньги не списывались.'
    for user in (booking.student, booking.slot.teacher.user):
        _notify(
            user,
            title='Урок не состоялся',
            short_text='К уроку никто не подключился.',
            full_text=(
                f'Урок не состоялся — к видеокомнате никто не подключился. '
                f'{money_text}'
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
    elif _booking_has_deposit(booking):
        title = 'Депозит сгорел из-за пропуска'
        warn = (
            f'Вы не подключились к уроку с {teacher}. Депозит за урок сгорел '
            f'и не возвращается — он начислен преподавателю.'
        )
        priority = 9
    else:
        title = 'Урок списан из-за пропуска'
        warn = (
            f'Вы пропустили урок с {teacher} ({ordinal}-й раз за 90 дней). '
            f'Урок списан из вашего пакета, оплата начислена преподавателю.'
        )
        priority = 9

    # Прощённая неявка по подписке: урок вернулся в квоту, добор новой даты —
    # на странице выбора расписания, а не в списке броней (аудит 2026-06-10 M15).
    if booking.no_show_forgiven and booking.subscription_id:
        action_url = _safe_url('subscription_schedule', booking.subscription_id)
    else:
        action_url = _safe_url('my_bookings_page')
    _notify(
        booking.student, title=title, short_text=warn[:120], full_text=warn,
        category=Notification.Category.WARNING, booking=booking, priority=priority,
        action_url=action_url,
    )
    LessonEvent.log(
        booking, 'warning_sent', actor='system',
        ordinal=ordinal, forgiven=booking.no_show_forgiven,
    )

    # Учителю — уведомление об итоге обработки урока. Текст соответствует
    # фактическому движению денег: прощённая неявка возвращает урок ученику
    # (выплаты нет), засчитанная (N+1) списывает урок и начисляет оплату учителю.
    student = booking.student.get_full_name() or booking.student.username
    if booking.no_show_forgiven:
        teacher_text = (
            f'Ученик {student} не подключился к уроку. По правилам сервиса урок '
            f'возвращён ученику, оплата за него не начисляется.'
        )
    else:
        teacher_text = (
            f'Ученик {student} не подключился к уроку. Урок засчитан, оплата '
            f'будет начислена вам в ближайшей выплате.'
        )
    _notify(
        booking.slot.teacher.user,
        title='Ученик не пришёл на урок',
        short_text=f'Ученик {student} не подключился к уроку.',
        full_text=teacher_text,
        category=Notification.Category.LESSON, booking=booking, priority=7,
        action_url=_safe_url('my_bookings_page'),
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
        else:
            # Разовый урок с депозитом — вина учителя → депозит возвращается.
            from billing.deposits import DepositService
            DepositService.refund(booking, reason='Учитель не подключился к уроку')
    except Exception as e:
        logger.error(
            f'_refund_teacher_no_show failed for booking {booking.pk}: {e}',
            exc_info=True,
        )


@shared_task(name='teachers.cleanup_old_inapp_notifications')
def cleanup_old_inapp_notifications(days: int = 90) -> dict:
    """Деактивация старых in-app уведомлений (аудит 2026-06-10 H15).

    teachers.Notification не чистилась никем: напоминания дают до 6 строк на
    урок, плюс платежи/модерация — бейдж (anti-join по всем активным) и листинг
    деградировали линейно, а каждый новый пользователь «наследовал» все старые
    broadcast как непрочитанные.

    Деактивируем (не удаляем — история/аудит остаются) всё старше `days` дней:
    get_unread_count/get_user_notifications фильтруют is_active=True, так что
    деактивированные сразу выпадают из горячих запросов.
    """
    from datetime import timedelta
    from django.utils import timezone as _tz
    from .models import Notification

    cutoff = _tz.now() - timedelta(days=days)
    updated = (
        Notification.objects
        .filter(is_active=True, created_at__lt=cutoff)
        .update(is_active=False)
    )
    if updated:
        logger.info('cleanup_old_inapp_notifications: deactivated %s', updated)
    return {'deactivated': updated}


@shared_task(name='teachers.replenish_teacher_slots')
def replenish_teacher_slots(weeks: int = 4, slot_minutes: int = 60) -> dict:
    """Поддерживает «скользящее окно» свободных слотов у активных учителей.

    Слоты нарезаются однократно при регистрации (registration_wizard.done →
    generate_slots_from_template на 4 недели). Без периодического пополнения
    окно «протухает»: через ~4 недели у учителя кончаются будущие слоты и
    календарь бронирования становится пустым, хотя weekly_schedule заполнен.

    Эта задача раз в сутки докручивает окно до `weeks` недель вперёд по
    ЛИЧНОМУ расписанию каждого учителя. generate_slots_from_template
    идемпотентна: прошлое и уже существующие/пересекающиеся слоты
    пропускаются — создаются только недостающие будущие free-слоты.
    Расписание учителя НЕ перезаписывается; учителя без weekly_schedule
    пропускаются.
    """
    from .models import TeacherProfile

    teachers = (
        TeacherProfile.objects
        .filter(is_active=True, moderation_status='approved')
        .iterator()
    )

    created_total = 0
    teachers_touched = 0
    for teacher in teachers:
        try:
            res = teacher.generate_slots_from_template(
                weeks=weeks, slot_minutes=slot_minutes,
            )
        except Exception as e:
            logger.error(
                'replenish_teacher_slots: failed for teacher %s: %s',
                teacher.pk, e, exc_info=True,
            )
            continue
        if res['created']:
            created_total += res['created']
            teachers_touched += 1

    if created_total:
        logger.info(
            'replenish_teacher_slots: created %s slots for %s teachers',
            created_total, teachers_touched,
        )
    return {'created': created_total, 'teachers': teachers_touched}


# =============================================================================
# Публикация нового преподавателя в Telegram-канал
# =============================================================================
# Живёт в teachers (не в telegram_bot), т.к. telegram_bot нет в INSTALLED_APPS
# и его tasks.py не попадает в autodiscover_tasks() celery-воркера.

@shared_task(name='teachers.publish_teacher_to_channel', bind=True,
             max_retries=5, default_retry_delay=60, time_limit=120)
def publish_teacher_to_channel(self, teacher_id):
    """Постит одобренного учителя в публичный канал. Идемпотентно: пропускает
    уже отправленные посты. При ошибке — retry с экспоненциальным backoff."""
    from django.utils import timezone
    from teachers.models import TeacherChannelPost
    from telegram_bot.channel_publisher import publish_teacher

    try:
        post = TeacherChannelPost.objects.select_related(
            'teacher__user', 'teacher__city'
        ).get(teacher_id=teacher_id)
    except TeacherChannelPost.DoesNotExist:
        logger.warning('TeacherChannelPost для учителя %s не найден', teacher_id)
        return None

    if post.status == 'sent':  # защита от повторной публикации
        logger.info('Учитель %s уже опубликован (message_id=%s)',
                    teacher_id, post.message_id)
        return post.message_id

    try:
        message_id = publish_teacher(post.teacher)
    except Exception as exc:
        post.attempts += 1
        post.status = 'failed'
        post.last_error = str(exc)[:2000]
        post.save(update_fields=['attempts', 'status', 'last_error'])
        logger.warning('Публикация учителя %s не удалась (попытка %s): %s',
                       teacher_id, self.request.retries + 1, exc)
        raise self.retry(exc=exc, countdown=min(60 * 2 ** self.request.retries, 3600))

    post.attempts += 1
    post.status = 'sent'
    post.message_id = message_id
    post.sent_at = timezone.now()
    post.last_error = ''
    post.save(update_fields=['attempts', 'status', 'message_id', 'sent_at', 'last_error'])
    logger.info('Учитель %s опубликован в канале, message_id=%s', teacher_id, message_id)
    return message_id


@shared_task(name='teachers.retry_failed_channel_posts', bind=True, time_limit=300)
def retry_failed_channel_posts(self):
    """Страховка: добирает публикации, упавшие после исчерпания ретраев задачи
    (например, при даунтайме воркера). Запускается периодически через Beat."""
    from django.db.models import F
    from teachers.models import TeacherChannelPost

    qs = TeacherChannelPost.objects.filter(
        status='failed', attempts__lt=F('max_attempts')
    )[:200]

    count = 0
    for post in qs:
        publish_teacher_to_channel.delay(post.teacher_id)
        count += 1

    if count:
        logger.info('Повторно поставлено в очередь %s публикаций в канал', count)
    return count
