"""
Доменный слой «Потенциальные ученики» (лиды).

Право учителя написать ученику ПЕРВЫМ — это привилегия, а не дефолт.
Учитель может инициировать переписку только с «лидом»:

    🔥 hot  — ученик забронировал пробный урок у этого учителя;
    ⭐ warm  — ученик добавил учителя в избранное.

Лиды НЕ хранятся отдельной таблицей — они выводятся из уже существующих
сущностей Favorite и Booking(is_trial=True). Это исключает рассинхрон
(добавил/убрал избранное, отменил пробный) и не дублирует данные.

Контроль ученика реализуется моделью LeadOptOut: ученик может в один тап
сказать «не интересно», и учитель теряет право инициировать переписку.
"""

from django.utils import timezone

LEAD_HOT = 'hot'
LEAD_WARM = 'warm'

# Человекочитаемые ярлыки статусов для UI.
LEAD_STATUS_LABELS = {
    LEAD_HOT: '🔥 Забронировал пробный урок',
    LEAD_WARM: '⭐ Добавил в избранное',
}


def _has_trial_booking(teacher_profile, student_user) -> bool:
    """Бронировал ли ученик пробный урок у этого учителя.

    Учитываем любую пробную бронь, кроме отменённой самим учеником —
    отмена учеником = отзыв интереса, держать такого в горячих лидах нечестно.
    """
    from .models import Booking
    return (
        Booking.objects
        .filter(
            slot__teacher=teacher_profile,
            student=student_user,
            is_trial=True,
        )
        .exclude(status='cancelled_by_student')
        .exists()
    )


def _has_favorite(teacher_profile, student_user) -> bool:
    """Добавил ли ученик учителя в избранное."""
    from .models import Favorite
    return Favorite.objects.filter(
        teacher=teacher_profile, student=student_user
    ).exists()


def is_opted_out(teacher_profile, student_user) -> bool:
    """Сказал ли ученик «не интересно» этому учителю."""
    from .models import LeadOptOut
    return LeadOptOut.objects.filter(
        teacher=teacher_profile, student=student_user
    ).exists()


def get_lead_status(teacher_profile, student_user):
    """Температура лида: LEAD_HOT / LEAD_WARM / None.

    Hot приоритетнее warm: если ученик и забронировал пробный, и в избранном —
    он горячий. Opt-out скрывает лид полностью (возвращаем None).
    """
    if student_user is None or getattr(student_user, 'user_type', None) != 'student':
        return None
    if is_opted_out(teacher_profile, student_user):
        return None
    if _has_trial_booking(teacher_profile, student_user):
        return LEAD_HOT
    if _has_favorite(teacher_profile, student_user):
        return LEAD_WARM
    return None


def can_teacher_initiate(teacher_profile, student_user) -> bool:
    """Имеет ли учитель право написать этому ученику ПЕРВЫМ.

    True только если ученик — действующий лид (hot/warm) и не сделал opt-out.
    Право проверяется ТОЛЬКО при инициации; в уже существующем чате
    (ученик ответил) это правило не действует — там обычная переписка.
    """
    return get_lead_status(teacher_profile, student_user) is not None


def student_has_replied(conversation) -> bool:
    """Отвечал ли ученик в этой переписке хотя бы раз."""
    return conversation.messages.filter(sender=conversation.student).exists()


def teacher_can_send_in_conversation(conversation):
    """Антиспам: может ли учитель отправить сообщение в чат прямо сейчас.

    Пока ученик не ответил, учитель вправе отправить только ОДНО первое
    сообщение. После первого ответа ученика чат становится обычным.
    Возвращает (bool, reason): reason='awaiting_student_reply' если заблокировано.
    На сообщения ученика это правило не распространяется — вызывать только
    когда отправитель = учитель.
    """
    if student_has_replied(conversation):
        return True, None
    teacher_user = conversation.teacher.user
    already_wrote = conversation.messages.filter(sender=teacher_user).exists()
    if already_wrote:
        return False, 'awaiting_student_reply'
    return True, None


def teacher_can_open_conversation(teacher_profile, student_user, conversation=None) -> bool:
    """Право учителя открыть/создать переписку с учеником.

    Разрешено, если ученик уже отвечал в существующем чате (обычная переписка)
    ИЛИ учитель имеет право инициировать (ученик — действующий лид).
    """
    if conversation is not None and student_has_replied(conversation):
        return True
    return can_teacher_initiate(teacher_profile, student_user)


def get_teacher_leads(teacher_profile):
    """Список лидов учителя, горячие сверху, внутри — по свежести интереса.

    Возвращает список dict:
        {student_user, student_profile, status, since}
    `since` — момент проявления интереса (бронь пробного / добавление в избранное).
    Учеников, сделавших opt-out, в списке нет.
    """
    from .models import Booking, Favorite, LeadOptOut

    opted_out_ids = set(
        LeadOptOut.objects
        .filter(teacher=teacher_profile)
        .values_list('student_id', flat=True)
    )

    # hot: пробные брони (свежесть = время самой ранней брони интереса)
    hot = {}
    trial_qs = (
        Booking.objects
        .filter(slot__teacher=teacher_profile, is_trial=True)
        .exclude(status='cancelled_by_student')
        .select_related('student', 'student__student_profile')
        .order_by('-created_at')
    )
    for b in trial_qs:
        sid = b.student_id
        if sid in opted_out_ids:
            continue
        # последняя по времени пробная бронь определяет «since»
        if sid not in hot:
            hot[sid] = {
                'student_user': b.student,
                'student_profile': getattr(b.student, 'student_profile', None),
                'status': LEAD_HOT,
                'since': b.created_at,
            }

    # warm: избранное, но только если ученик ещё не горяч
    warm = {}
    fav_qs = (
        Favorite.objects
        .filter(teacher=teacher_profile)
        .select_related('student', 'student__student_profile')
        .order_by('-created_at')
    )
    for f in fav_qs:
        sid = f.student_id
        if sid in opted_out_ids or sid in hot:
            continue
        if sid not in warm:
            warm[sid] = {
                'student_user': f.student,
                'student_profile': getattr(f.student, 'student_profile', None),
                'status': LEAD_WARM,
                'since': f.created_at,
            }

    hot_list = sorted(hot.values(), key=lambda x: x['since'], reverse=True)
    warm_list = sorted(warm.values(), key=lambda x: x['since'], reverse=True)
    return hot_list + warm_list


def _count_new(leads, seen_at):
    """Сколько лидов проявили интерес ПОЗЖЕ метки просмотра seen_at.

    seen_at=None (учитель ни разу не открывал раздел) → все лиды новые.
    """
    if seen_at is None:
        return len(leads)
    return sum(1 for l in leads if l['since'] and l['since'] > seen_at)


def _lead_counts(leads, seen_at):
    hot = sum(1 for l in leads if l['status'] == LEAD_HOT)
    warm = sum(1 for l in leads if l['status'] == LEAD_WARM)
    return {'hot': hot, 'warm': warm, 'total': hot + warm,
            'new': _count_new(leads, seen_at)}


def count_teacher_leads(teacher_profile, leads=None):
    """Счётчики для бейджей: {'hot', 'warm', 'total', 'new'}.

    'new' — число непросмотренных лидов (интерес свежее leads_seen_at) для
    индикатора на кнопке; гаснет после открытия раздела (watermark сдвигается),
    загорается снова при новом интересе.

    leads — уже загруженный get_teacher_leads(...) (чтобы не грузить все лиды
    второй раз на той же странице). Без него — кэш 60с: бейдж дёргается на
    каждый показ профиля учителя, а get_teacher_leads загружает ВСЕ trial-брони
    и избранное без лимита (аудит 2026-06-10 M7). 'new' и 'total' считаются за
    ОДИН проход get_teacher_leads и кэшируются вместе; кэш сбрасывается при
    изменении источников лидов (signals) и при сдвиге watermark (открытие
    раздела), поэтому отдельного кэша под индикатор не нужно.
    """
    seen_at = teacher_profile.leads_seen_at
    if leads is not None:
        return _lead_counts(leads, seen_at)
    from django.core.cache import cache
    cache_key = f'teacher_lead_counts_{teacher_profile.pk}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    counts = _lead_counts(get_teacher_leads(teacher_profile), seen_at)
    cache.set(cache_key, counts, 60)
    return counts


def count_new_teacher_leads(teacher_profile, leads=None):
    """Число НОВЫХ (непросмотренных) лидов для индикатора. Тонкая обёртка над
    count_teacher_leads — единый источник и единый кэш-ключ."""
    return count_teacher_leads(teacher_profile, leads=leads)['new']
