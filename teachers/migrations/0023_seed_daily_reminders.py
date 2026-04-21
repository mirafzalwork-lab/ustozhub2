"""
Засеять дефолтные шаблоны ежедневных напоминаний (ru/uz/en × утро/вечер),
чтобы админ сразу увидел готовые варианты в admin-dashboard
и мог их редактировать.
"""

from django.db import migrations


SEED = {
    'morning': {
        'ru': [
            "☀️ *Доброе утро!*\n\n"
            "Новый день — новые возможности на *UstozHub*.\n\n"
            "📚 Загляните на платформу: найдите учителя, проверьте сообщения "
            "или обновите свой профиль.\n\n"
            "_Пусть этот день принесёт вам знания и успех!_",

            "🌅 *С добрым утром!*\n\n"
            "На *UstozHub* вас уже могут искать ученики или учителя.\n\n"
            "✨ Проверьте входящие, ответьте на запросы и не упустите свой шанс.\n\n"
            "_Хорошего и продуктивного дня!_",

            "☕ *Доброе утро!*\n\n"
            "Начните день с одного шага навстречу цели — зайдите на *UstozHub*.\n\n"
            "🎯 Там может ждать новое сообщение или подходящий учитель.\n\n"
            "_Успехов и вдохновения!_",
        ],
        'uz': [
            "☀️ *Xayrli tong!*\n\n"
            "Yangi kun — *UstozHub*'da yangi imkoniyatlar.\n\n"
            "📚 Platformaga kiring: o‘qituvchi toping, xabarlarni tekshiring "
            "yoki profilingizni yangilang.\n\n"
            "_Bugun sizga bilim va omad keltirsin!_",

            "🌅 *Xayrli tong!*\n\n"
            "*UstozHub*'da sizni allaqachon o‘quvchi yoki ustoz kutayotgan bo‘lishi mumkin.\n\n"
            "✨ Kelgan xabarlarni ko‘ring va imkoniyatlarni qo‘ldan boy bermang.\n\n"
            "_Sizga samarali kun tilaymiz!_",

            "☕ *Xayrli tong!*\n\n"
            "Kuningizni maqsad sari bir qadam bilan boshlang — *UstozHub*'ga kiring.\n\n"
            "🎯 U yerda sizni yangi xabar yoki munosib ustoz kutayotgan bo‘lishi mumkin.\n\n"
            "_Omad va ilhom tilaymiz!_",
        ],
        'en': [
            "☀️ *Good morning!*\n\n"
            "A new day brings new opportunities on *UstozHub*.\n\n"
            "📚 Open the platform: find a teacher, check your messages "
            "or update your profile.\n\n"
            "_Have a productive day full of learning!_",

            "🌅 *Good morning!*\n\n"
            "On *UstozHub* students or teachers may already be looking for you.\n\n"
            "✨ Check your inbox, reply to requests and don't miss your chance.\n\n"
            "_Wishing you a great day ahead!_",
        ],
    },
    'evening': {
        'ru': [
            "🌙 *Добрый вечер!*\n\n"
            "День подходит к концу — отличное время заглянуть на *UstozHub*.\n\n"
            "📨 Проверьте непрочитанные сообщения и отзывы.\n"
            "📈 Уделите пару минут своему профилю — это повышает шансы быть замеченным.\n\n"
            "_Спокойной и продуктивной ночи!_",

            "✨ *Добрый вечер!*\n\n"
            "Не забудьте заглянуть на *UstozHub* перед тем, как отдохнуть.\n\n"
            "💬 Возможно, вам пришло сообщение, которое изменит ваш следующий день.\n\n"
            "_Приятного вечера!_",

            "🌆 *Добрый вечер!*\n\n"
            "Потратьте 2 минуты на *UstozHub* — ответьте на сообщения и просмотрите новости.\n\n"
            "🎓 Маленькие шаги каждый день — большой результат через месяц.\n\n"
            "_Хорошего отдыха!_",
        ],
        'uz': [
            "🌙 *Xayrli kech!*\n\n"
            "Kun yakuniga yaqin — *UstozHub*'ga kirish uchun ajoyib vaqt.\n\n"
            "📨 O‘qilmagan xabarlar va fikr-mulohazalarni ko‘rib chiqing.\n"
            "📈 Profilingizga bir necha daqiqa ajrating — bu sizni ko‘zga ko‘rinarli qiladi.\n\n"
            "_Tinch va samarali kech tilaymiz!_",

            "✨ *Xayrli kech!*\n\n"
            "Dam olishdan oldin *UstozHub*'ga kirishni unutmang.\n\n"
            "💬 Ertangi kuningizni o‘zgartiradigan xabar sizni kutayotgan bo‘lishi mumkin.\n\n"
            "_Yaxshi kech tilaymiz!_",

            "🌆 *Xayrli kech!*\n\n"
            "*UstozHub*'ga 2 daqiqa ajrating — xabarlarga javob bering va yangiliklarni ko‘ring.\n\n"
            "🎓 Har kungi kichik qadamlar — bir oyda katta natija.\n\n"
            "_Yaxshi dam oling!_",
        ],
        'en': [
            "🌙 *Good evening!*\n\n"
            "The day is ending — a perfect moment to visit *UstozHub*.\n\n"
            "📨 Check your unread messages and reviews.\n"
            "📈 Spend a few minutes on your profile — it helps you stand out.\n\n"
            "_Have a calm and productive night!_",

            "✨ *Good evening!*\n\n"
            "Don't forget to stop by *UstozHub* before you rest.\n\n"
            "💬 A message that could change your next day may be waiting for you.\n\n"
            "_Have a pleasant evening!_",
        ],
    },
}


def seed_templates(apps, schema_editor):
    DailyReminderTemplate = apps.get_model('teachers', 'DailyReminderTemplate')
    # Не перезаписываем, если админ уже что-то создал
    if DailyReminderTemplate.objects.exists():
        return
    to_create = []
    for period, by_lang in SEED.items():
        for lang, texts in by_lang.items():
            for text in texts:
                to_create.append(DailyReminderTemplate(
                    period=period,
                    language=lang,
                    text=text,
                    is_active=True,
                    note='Стандартный вариант (seed)',
                ))
    DailyReminderTemplate.objects.bulk_create(to_create)


def noop_reverse(apps, schema_editor):
    # При откате ничего не трогаем — админ мог отредактировать
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('teachers', '0022_dailyremindertemplate'),
    ]

    operations = [
        migrations.RunPython(seed_templates, noop_reverse),
    ]
