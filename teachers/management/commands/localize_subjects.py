"""
Заполняет name_uz / name_en у предметов (для локализации названий при смене
языка). Проприетарные/англоязычные названия (Python, IELTS, Frontend…) можно
не переводить — у них fallback на базовое name.

Идемпотентна: не перезаписывает уже заполненные поля. По умолчанию dry-run.
"""
from django.core.management.base import BaseCommand

from teachers.models import Subject


# base name -> (uz, en)
TRANSLATIONS = {
    "Математика": ("Matematika", "Mathematics"),
    "Русский язык": ("Rus tili", "Russian"),
    "Немецкий язык": ("Nemis tili", "German"),
    "Физика": ("Fizika", "Physics"),
    "Химия": ("Kimyo", "Chemistry"),
    "Французский язык": ("Fransuz tili", "French"),
    "Arab tili": ("Arab tili", "Arabic"),
    "Ona tili": ("Ona tili", "Mother tongue (Uzbek)"),
    "Biologiya": ("Biologiya", "Biology"),
    "English": ("Ingliz tili", "English"),
    "Korean language": ("Koreys tili", "Korean"),
    "Turkish": ("Turk tili", "Turkish"),
    "Turk tili | Турецкий язык": ("Turk tili", "Turkish"),
    "Tarix | история": ("Tarix", "History"),
    "Информатика | Informatika": ("Informatika", "Computer science"),
    "Kompyuter savodxonligi": ("Kompyuter savodxonligi", "Computer literacy"),
    "SQL / Базы данных": ("SQL / Ma'lumotlar bazasi", "SQL / Databases"),
    "Промпт-инжиниринг": ("Prompt muhandisligi", "Prompt engineering"),
    "Yapon tili | японский язык": ("Yapon tili", "Japanese"),
}


class Command(BaseCommand):
    help = "Заполняет name_uz/name_en у предметов. Dry-run по умолчанию, --apply для записи."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **opts):
        apply = opts['apply']
        log = self.stdout.write
        done = 0
        for name, (uz, en) in TRANSLATIONS.items():
            s = Subject.objects.filter(name=name).first()
            if not s:
                log(f"  · пропуск (нет): {name!r}")
                continue
            changed = []
            if uz and not s.name_uz:
                changed.append(f"uz={uz!r}")
            if en and not s.name_en:
                changed.append(f"en={en!r}")
            if not changed:
                continue
            log(f"  · {name!r}: {', '.join(changed)}")
            if apply:
                if uz and not s.name_uz:
                    s.name_uz = uz
                if en and not s.name_en:
                    s.name_en = en
                s.save()  # пересоберёт search_text со всеми языками
                done += 1
        if not apply:
            log(self.style.WARNING("\nDRY-RUN. Запусти с --apply для записи."))
        else:
            log(self.style.SUCCESS(f"\nГотово. Обновлено предметов: {done}"))
