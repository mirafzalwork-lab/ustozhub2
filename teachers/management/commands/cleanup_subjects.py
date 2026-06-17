"""
Нормализация предметов на проде: переименования (опечатки/регистр),
слияние дубликатов (перенос учителей на канонический предмет), удаление мусора.

По умолчанию DRY-RUN (ничего не пишет). Запись: --apply

Слияние безопасно: TeacherSubject (с ценами) переносится на канонический предмет
с учётом unique(teacher, subject); прочие ссылки (Conversation/Review/Booking/…)
репойнтятся; затем дубль удаляется. Удаляются ТОЛЬКО предметы с 0 учителей.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from teachers.models import Subject, TeacherSubject


# Переименования на месте (исправление опечаток/регистра). Пропускаются, если цель уже есть.
RENAMES = [
    ("математика", "Математика"),
    ("русский язык", "Русский язык"),
    ("физика", "Физика"),
    ("js", "JavaScript"),
    ("Oracl DBA", "Oracle DBA"),
    ("Промт инжиниринг", "Промпт-инжиниринг"),
    ("Turk tili | Турецикий язык", "Turk tili | Турецкий язык"),
    ("Fransuz Tili", "Французский язык"),
    ("HTML & CSS", "HTML / CSS"),
    ("Ona Tili", "Ona tili"),
]

# Слияние: канонический ← [источники]. Выполняется ПОСЛЕ переименований.
MERGES = [
    ("Немецкий язык", ["немецкий язык"]),
    ("Химия", ["Chemistry", "Kimyo"]),
    ("Turk tili | Турецкий язык", ["Turkish"]),
    ("Microsoft Word / Excel / PowerPoint", ["Word, Exel"]),
    ("Oracle DBA", ["Data Base Administration"]),
    ("Graphic Design (Photoshop, Illustrator, Figma)", ["Photoshop / Illustrator"]),
]

# Удаление мусора — только если у предмета 0 учителей (иначе ОТКАЗ).
DELETES = [
    "html", "css", "html,css js", "react",
    "Shadcn UI, JavaScript, React.js, TypeScript,",
    "Mabilograf",
]


def n_teachers(subj):
    return TeacherSubject.objects.filter(subject=subj).count()


class Command(BaseCommand):
    help = "Нормализация предметов (переименования/слияния/удаление мусора). Dry-run по умолчанию."

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Реально записать. Без флага — dry-run.')

    def _get(self, name):
        return Subject.objects.filter(name=name).first()

    def handle(self, *args, **opts):
        apply = opts['apply']
        log = self.stdout.write

        log("===== ПЕРЕИМЕНОВАНИЯ =====")
        for old, new in RENAMES:
            s = self._get(old)
            if not s:
                log(f"  · пропуск (нет): {old!r}"); continue
            if self._get(new):
                log(f"  · пропуск (цель {new!r} уже есть → слияние ниже): {old!r}"); continue
            log(f"  · {old!r} → {new!r}  (учителей: {n_teachers(s)})")
            if apply:
                s.name = new
                s.save()  # пересоберёт search_text

        log("===== СЛИЯНИЯ =====")
        for canon_name, sources in MERGES:
            canon = self._get(canon_name)
            if not canon:
                log(f"  · создаю канонический предмет: {canon_name!r}")
                if apply:
                    canon = Subject.objects.create(name=canon_name, is_active=True)
            for sname in sources:
                s = self._get(sname)
                if not s:
                    log(f"    – пропуск (нет источника): {sname!r}"); continue
                if canon and s.pk == canon.pk:
                    continue
                nt = n_teachers(s)
                if apply and canon:
                    with transaction.atomic():
                        moved, dropped = self._merge(s, canon)
                    log(f"    – {sname!r} → {canon_name!r}: перенесено {moved}, дублей снято {dropped}, удалён")
                else:
                    log(f"    – {sname!r} ({nt} учит.) → {canon_name!r}")

        log("===== УДАЛЕНИЕ МУСОРА (только пустые) =====")
        for name in DELETES:
            s = self._get(name)
            if not s:
                log(f"  · пропуск (нет): {name!r}"); continue
            nt = n_teachers(s)
            if nt > 0:
                log(f"  · ОТКАЗ ({nt} учит., не мусор): {name!r}"); continue
            log(f"  · удаляю: {name!r}")
            if apply:
                s.delete()  # SET_NULL ссылки обнулятся, TeacherSubject нет (0 учителей)

        if not apply:
            log(self.style.WARNING("\nDRY-RUN — ничего не записано. Запусти с --apply."))
        else:
            total = Subject.objects.count()
            log(self.style.SUCCESS(f"\nГотово. Предметов осталось: {total}"))

    def _merge(self, s, canon):
        """Переносит учителей и ссылки с s на canon, удаляет s. Возвращает (moved, dropped)."""
        moved = dropped = 0
        for ts in TeacherSubject.objects.filter(subject=s):
            if TeacherSubject.objects.filter(teacher_id=ts.teacher_id, subject=canon).exists():
                ts.delete(); dropped += 1   # у учителя уже есть канонический — снимаем дубль
            else:
                ts.subject = canon; ts.save(update_fields=['subject']); moved += 1
        # Репойнт всех прочих FK на Subject (Conversation/Review/Booking/selected_subject/…)
        for rel in s._meta.related_objects:
            if rel.many_to_many or rel.related_model is TeacherSubject:
                continue
            fk = rel.field.name
            rel.related_model.objects.filter(**{fk: s}).update(**{fk: canon})
        s.delete()
        return moved, dropped
