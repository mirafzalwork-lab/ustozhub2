"""
Ежедневная рассылка напоминаний всем пользователям Telegram-бота
(утреннее и вечернее). Текст подбирается по языку, указанному в
TelegramUser.language_code (ru / uz / en).

Использование:
    python manage.py send_daily_reminder --period morning
    python manage.py send_daily_reminder --period evening
    python manage.py send_daily_reminder --period auto   # по текущему времени
    python manage.py send_daily_reminder --period morning --dry-run

Рекомендация по cron (TIME_ZONE = Asia/Tashkent):
    0  8  * * *   cd /path/to/project && /path/to/venv/bin/python manage.py send_daily_reminder --period morning
    0  20 * * *   cd /path/to/project && /path/to/venv/bin/python manage.py send_daily_reminder --period evening
"""

import logging
import random
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from teachers.models import TelegramUser, DailyReminderTemplate
from teachers.admin_telegram_service import admin_telegram_service

logger = logging.getLogger(__name__)


SUPPORTED_LANGS = ('ru', 'uz', 'en')


# =========================================================================
# ТЕКСТЫ СООБЩЕНИЙ
# Markdown parse_mode: используем *жирный* и _курсив_.
# Поддерживается несколько вариантов, чтобы сообщения не выглядели шаблонно.
# =========================================================================

MESSAGES = {
    'morning': {
        'ru': [
            (
                "☀️ *Доброе утро!*\n\n"
                "Новый день — новые возможности на *UstozHub*.\n\n"
                "📚 Загляните на платформу: найдите учителя, проверьте сообщения "
                "или обновите свой профиль.\n\n"
                "_Пусть этот день принесёт вам знания и успех!_"
            ),
            (
                "🌅 *С добрым утром!*\n\n"
                "На *UstozHub* вас уже могут искать ученики или учителя.\n\n"
                "✨ Проверьте входящие, ответьте на запросы и не упустите свой шанс.\n\n"
                "_Хорошего и продуктивного дня!_"
            ),
            (
                "☕ *Доброе утро!*\n\n"
                "Начните день с одного шага навстречу цели — зайдите на *UstozHub*.\n\n"
                "🎯 Там может ждать новое сообщение или подходящий учитель.\n\n"
                "_Успехов и вдохновения!_"
            ),
        ],
        'uz': [
            (
                "☀️ *Xayrli tong!*\n\n"
                "Yangi kun — *UstozHub*'da yangi imkoniyatlar.\n\n"
                "📚 Platformaga kiring: o‘qituvchi toping, xabarlarni tekshiring "
                "yoki profilingizni yangilang.\n\n"
                "_Bugun sizga bilim va omad keltirsin!_"
            ),
            (
                "🌅 *Xayrli tong!*\n\n"
                "*UstozHub*'da sizni allaqachon o‘quvchi yoki ustoz kutayotgan bo‘lishi mumkin.\n\n"
                "✨ Kelgan xabarlarni ko‘ring va imkoniyatlarni qo‘ldan boy bermang.\n\n"
                "_Sizga samarali kun tilaymiz!_"
            ),
            (
                "☕ *Xayrli tong!*\n\n"
                "Kuningizni maqsad sari bir qadam bilan boshlang — *UstozHub*'ga kiring.\n\n"
                "🎯 U yerda sizni yangi xabar yoki munosib ustoz kutayotgan bo‘lishi mumkin.\n\n"
                "_Omad va ilhom tilaymiz!_"
            ),
        ],
        'en': [
            (
                "☀️ *Good morning!*\n\n"
                "A new day brings new opportunities on *UstozHub*.\n\n"
                "📚 Open the platform: find a teacher, check your messages "
                "or update your profile.\n\n"
                "_Have a productive day full of learning!_"
            ),
            (
                "🌅 *Good morning!*\n\n"
                "On *UstozHub* students or teachers may already be looking for you.\n\n"
                "✨ Check your inbox, reply to requests and don't miss your chance.\n\n"
                "_Wishing you a great day ahead!_"
            ),
        ],
    },

    'evening': {
        'ru': [
            (
                "🌙 *Добрый вечер!*\n\n"
                "День подходит к концу — отличное время заглянуть на *UstozHub*.\n\n"
                "📨 Проверьте непрочитанные сообщения и отзывы.\n"
                "📈 Уделите пару минут своему профилю — это повышает шансы быть замеченным.\n\n"
                "_Спокойной и продуктивной ночи!_"
            ),
            (
                "✨ *Добрый вечер!*\n\n"
                "Не забудьте заглянуть на *UstozHub* перед тем, как отдохнуть.\n\n"
                "💬 Возможно, вам пришло сообщение, которое изменит ваш следующий день.\n\n"
                "_Приятного вечера!_"
            ),
            (
                "🌆 *Добрый вечер!*\n\n"
                "Потратьте 2 минуты на *UstozHub* — ответьте на сообщения и просмотрите новости.\n\n"
                "🎓 Маленькие шаги каждый день — большой результат через месяц.\n\n"
                "_Хорошего отдыха!_"
            ),
        ],
        'uz': [
            (
                "🌙 *Xayrli kech!*\n\n"
                "Kun yakuniga yaqin — *UstozHub*'ga kirish uchun ajoyib vaqt.\n\n"
                "📨 O‘qilmagan xabarlar va fikr-mulohazalarni ko‘rib chiqing.\n"
                "📈 Profilingizga bir necha daqiqa ajrating — bu sizni ko‘zga ko‘rinarli qiladi.\n\n"
                "_Tinch va samarali kech tilaymiz!_"
            ),
            (
                "✨ *Xayrli kech!*\n\n"
                "Dam olishdan oldin *UstozHub*'ga kirishni unutmang.\n\n"
                "💬 Ertangi kuningizni o‘zgartiradigan xabar sizni kutayotgan bo‘lishi mumkin.\n\n"
                "_Yaxshi kech tilaymiz!_"
            ),
            (
                "🌆 *Xayrli kech!*\n\n"
                "*UstozHub*'ga 2 daqiqa ajrating — xabarlarga javob bering va yangiliklarni ko‘ring.\n\n"
                "🎓 Har kungi kichik qadamlar — bir oyda katta natija.\n\n"
                "_Yaxshi dam oling!_"
            ),
        ],
        'en': [
            (
                "🌙 *Good evening!*\n\n"
                "The day is ending — a perfect moment to visit *UstozHub*.\n\n"
                "📨 Check your unread messages and reviews.\n"
                "📈 Spend a few minutes on your profile — it helps you stand out.\n\n"
                "_Have a calm and productive night!_"
            ),
            (
                "✨ *Good evening!*\n\n"
                "Don't forget to stop by *UstozHub* before you rest.\n\n"
                "💬 A message that could change your next day may be waiting for you.\n\n"
                "_Have a pleasant evening!_"
            ),
        ],
    },
}


FOOTER_BUTTON = {
    'ru': '🌐 Открыть UstozHub',
    'uz': '🌐 UstozHub’ni ochish',
    'en': '🌐 Open UstozHub',
}


def normalize_lang(code: str | None) -> str:
    """Привести language_code пользователя к одному из поддерживаемых."""
    if not code:
        return 'ru'
    code = code.lower().strip()
    # 'ru', 'ru-ru' -> 'ru'
    short = code.split('-')[0]
    if short in SUPPORTED_LANGS:
        return short
    # Узбекский латиницей/кириллицей
    if short in ('uz', 'uzb', 'oz'):
        return 'uz'
    return 'ru'


def load_db_templates(period: str) -> dict[str, list[str]]:
    """
    Загрузить активные шаблоны из БД для указанного периода,
    сгруппированные по языку.
    """
    qs = DailyReminderTemplate.objects.filter(
        period=period, is_active=True,
    ).values_list('language', 'text')
    out: dict[str, list[str]] = {}
    for lang, text in qs:
        out.setdefault(lang, []).append(text)
    return out


def pick_text(period: str, lang: str, db_cache: dict[str, list[str]]) -> str:
    """
    Вернуть случайный вариант текста для периода и языка.
    Приоритет: активные шаблоны из БД → fallback на зашитые MESSAGES → русский fallback.
    """
    # 1. БД для нужного языка
    variants = db_cache.get(lang)
    if variants:
        return random.choice(variants)
    # 2. БД на русском (если нужный язык пуст)
    variants = db_cache.get('ru')
    if variants:
        return random.choice(variants)
    # 3. Зашитый fallback
    variants = MESSAGES[period].get(lang) or MESSAGES[period]['ru']
    return random.choice(variants)


def auto_period() -> str:
    """Определить период по текущему часу: 5–14 — morning, иначе — evening."""
    hour = timezone.localtime().hour
    return 'morning' if 5 <= hour < 15 else 'evening'


class Command(BaseCommand):
    help = (
        'Ежедневная рассылка утренних/вечерних напоминаний всем пользователям '
        'Telegram-бота UstozHub. Текст подбирается по языку пользователя.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--period',
            choices=['morning', 'evening', 'auto'],
            default='auto',
            help='Какое напоминание отправлять (по умолчанию auto — по текущему времени).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Не отправлять сообщения, а только показать, что было бы отправлено.',
        )
        parser.add_argument(
            '--user-type',
            choices=['teacher', 'student'],
            default=None,
            help='Ограничить рассылку конкретным типом пользователей (по User.user_type).',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Максимальное число получателей (0 = без ограничений).',
        )

    def handle(self, *args, **options):
        period = options['period']
        if period == 'auto':
            period = auto_period()

        if period not in ('morning', 'evening'):
            raise CommandError(f"Некорректный период: {period}")

        dry_run: bool = options['dry_run']
        user_type: str | None = options['user_type']
        limit: int = options['limit']

        queryset = TelegramUser.objects.filter(
            started_bot=True,
            notifications_enabled=True,
        ).select_related('user')

        if user_type:
            queryset = queryset.filter(user__user_type=user_type)

        queryset = queryset.order_by('id')

        if limit and limit > 0:
            queryset = queryset[:limit]

        total = queryset.count() if not limit else len(list(queryset))
        self.stdout.write(self.style.NOTICE(
            f"📅 Период: {period} | Получателей: {total} | dry_run={dry_run}"
        ))

        if total == 0:
            self.stdout.write(self.style.WARNING("Нет пользователей для рассылки."))
            return

        if not admin_telegram_service.bot and not dry_run:
            raise CommandError(
                "Telegram bot не инициализирован (проверьте TELEGRAM_BOT_TOKEN)."
            )

        # Предзагружаем активные шаблоны из БД один раз на всю рассылку
        db_templates = load_db_templates(period)
        if db_templates:
            self.stdout.write(self.style.NOTICE(
                f"📝 Шаблонов из БД: "
                + ", ".join(f"{l}×{len(v)}" for l, v in db_templates.items())
            ))
        else:
            self.stdout.write(self.style.WARNING(
                "📝 В БД нет активных шаблонов — используются встроенные (fallback)."
            ))

        stats = {'success': 0, 'failed': 0, 'skipped': 0, 'by_lang': {}}

        for tg_user in queryset.iterator():
            lang = normalize_lang(tg_user.language_code)
            stats['by_lang'][lang] = stats['by_lang'].get(lang, 0) + 1
            text = pick_text(period, lang, db_templates)

            if dry_run:
                preview = text.splitlines()[0][:60]
                self.stdout.write(
                    f"  [DRY] {tg_user.telegram_id} ({lang}): {preview}…"
                )
                stats['success'] += 1
                continue

            try:
                ok = admin_telegram_service.send_message_simple(
                    telegram_id=tg_user.telegram_id,
                    text=text,
                    parse_mode='Markdown',
                )
                if ok:
                    stats['success'] += 1
                else:
                    stats['failed'] += 1
            except Exception as exc:
                logger.exception(
                    "Daily reminder: ошибка отправки пользователю %s: %s",
                    tg_user.telegram_id, exc,
                )
                stats['failed'] += 1

        self.stdout.write(self.style.SUCCESS(
            f"✅ Готово. Период={period}, "
            f"sent={stats['success']}, failed={stats['failed']}, "
            f"by_lang={stats['by_lang']}"
        ))
        logger.info(
            "Daily reminder [%s] finished: sent=%s failed=%s by_lang=%s",
            period, stats['success'], stats['failed'], stats['by_lang'],
        )
