"""
Генерация слотов по умолчанию учителям, у которых НЕТ ни одного слота.

Расписание (по решению владельца):
  Пн–Пт  19:00–21:00  (по 60 мин → 2 слота/день)
  Сб     14:00–18:00  (по 60 мин → 4 слота/день)
  Вс     выходной

Только активные и одобренные учителя. Учителя, у которых есть хотя бы один
слот (любого статуса), НЕ трогаются.

Логика нарезки переиспользует TeacherProfile.generate_slots_from_template():
прошлое и пересечения пропускаются, exclusion-констрейнт ловит гонки.

По умолчанию — DRY-RUN (ничего не пишет). Для реальной записи: --apply
"""
from django.core.management.base import BaseCommand
from django.db.models import Count

from teachers.models import TeacherProfile


# Стандартный шаблон расписания (новый формат weekly_schedule)
DEFAULT_SCHEDULE = {
    'monday':    [{'from': '19:00', 'to': '21:00'}],
    'tuesday':   [{'from': '19:00', 'to': '21:00'}],
    'wednesday': [{'from': '19:00', 'to': '21:00'}],
    'thursday':  [{'from': '19:00', 'to': '21:00'}],
    'friday':    [{'from': '19:00', 'to': '21:00'}],
    'saturday':  [{'from': '14:00', 'to': '18:00'}],
    'sunday':    [],
}


class Command(BaseCommand):
    help = ('Генерирует стандартные слоты учителям без единого слота '
            '(Пн–Пт 19:00–21:00, Сб 14:00–18:00). По умолчанию dry-run.')

    def add_arguments(self, parser):
        parser.add_argument('--weeks', type=int, default=4,
                            help='На сколько недель вперёд (1–12). По умолчанию 4.')
        parser.add_argument('--slot-minutes', type=int, default=60,
                            help='Длительность слота (30/45/60/90/120). По умолчанию 60.')
        parser.add_argument('--apply', action='store_true',
                            help='Реально записать в БД. Без флага — только показать (dry-run).')

    def handle(self, *args, **opts):
        weeks = opts['weeks']
        slot_minutes = opts['slot_minutes']
        apply = opts['apply']

        # Целевые: активные, одобренные, с НУЛЁМ слотов.
        targets = (
            TeacherProfile.objects
            .filter(is_active=True, moderation_status='approved')
            .annotate(n_slots=Count('time_slots'))
            .filter(n_slots=0)
            .select_related('user')
            .order_by('id')
        )
        targets = list(targets)  # фиксируем снимок (в --apply будем менять данные по ходу)
        total = len(targets)
        with_schedule = sum(1 for t in targets if t.has_schedule())

        self.stdout.write(f'Активных/одобренных учителей без слотов: {total}')
        self.stdout.write(f'  из них с уже заданным шаблоном расписания: {with_schedule} '
                          f'(будет заменён на стандартный)')
        self.stdout.write(f'Расписание: Пн–Пт 19:00–21:00, Сб 14:00–18:00 · '
                          f'{slot_minutes} мин · {weeks} нед · старт с завтра')

        if not apply:
            per_week = 5 * 2 + 4  # 14 слотов/нед при 60-мин шаге
            self.stdout.write(self.style.WARNING(
                f'\nDRY-RUN — ничего не записано. Ориентировочно будет создано '
                f'до ~{total * per_week * weeks} слотов ({per_week}/нед × {weeks} нед × {total} учит.).\n'
                f'Примеры учителей:'))
            for t in targets[:15]:
                name = t.user.get_full_name() or t.user.username
                self.stdout.write(f'  - id={t.id}  {name}')
            if total > 15:
                self.stdout.write(f'  … и ещё {total - 15}')
            self.stdout.write(self.style.WARNING('\nЗапусти с --apply, чтобы записать.'))
            return

        created_total = 0
        teachers_done = 0
        for t in targets:
            t.weekly_schedule = DEFAULT_SCHEDULE
            t.save(update_fields=['weekly_schedule'])
            res = t.generate_slots_from_template(weeks=weeks, slot_minutes=slot_minutes)
            created_total += res['created']
            teachers_done += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nГотово. Учителей обработано: {teachers_done}, создано слотов: {created_total}'))
