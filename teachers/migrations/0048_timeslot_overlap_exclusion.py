# Аудит 2026-06-10 H12: пересечение слотов расписания не было защищено на
# уровне БД. Python-проверки (slots_create_api, bulk_generate,
# generate_slots_from_template) не лочат «отсутствие» строк: два параллельных
# POST с взаимно пересекающимися интервалами оба проходят проверку → двойные
# слоты → двойные брони одного времени.
#
# PostgreSQL: EXCLUDE USING gist (teacher_id WITH =, tstzrange WITH &&).
# SQLite (dev): no-op — гонку там прикрывает только python-проверка.
#
# Перед созданием констрейнта чистим существующие пересечения БЕЗОПАСНО:
# удаляем только free-слоты без истории броней (Booking.slot = PROTECT всё
# равно не даст удалить остальные). Если после чистки пересечения остались
# (booked/blocked слоты) — констрейнт НЕ создаётся, пишется CRITICAL: ручной
# разбор + повторный migrate. Падать нельзя — это заблокировало бы деплой.

from django.db import migrations

CONSTRAINT = 'excl_teacher_slot_overlap'


def _find_overlapping_ids(schema_editor):
    """Пары пересекающихся слотов одного учителя (id растущего порядка)."""
    with schema_editor.connection.cursor() as cur:
        cur.execute(
            '''
            SELECT a.id, b.id
            FROM teachers_timeslot a
            JOIN teachers_timeslot b
              ON a.teacher_id = b.teacher_id
             AND a.id < b.id
             AND a.start_at < b.end_at
             AND b.start_at < a.end_at
            '''
        )
        return cur.fetchall()


def add_exclusion_constraint(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return

    import logging
    logger = logging.getLogger(__name__)

    schema_editor.execute('CREATE EXTENSION IF NOT EXISTS btree_gist')

    pairs = _find_overlapping_ids(schema_editor)
    if pairs:
        # Безопасная чистка: из каждой пары пытаемся удалить дубликат-free
        # без броней (оставляя первый/занятый).
        candidate_ids = sorted({b for _a, b in pairs} | {a for a, _b in pairs})
        with schema_editor.connection.cursor() as cur:
            cur.execute(
                '''
                DELETE FROM teachers_timeslot t
                WHERE t.id = ANY(%s)
                  AND t.status = 'free'
                  AND NOT EXISTS (
                      SELECT 1 FROM teachers_booking bk WHERE bk.slot_id = t.id
                  )
                  AND EXISTS (
                      SELECT 1 FROM teachers_timeslot o
                      WHERE o.teacher_id = t.teacher_id
                        AND o.id < t.id
                        AND o.start_at < t.end_at
                        AND t.start_at < o.end_at
                  )
                ''',
                [candidate_ids],
            )
            logger.warning(
                'timeslot overlap cleanup: удалено %s пересекающихся free-слотов',
                cur.rowcount,
            )
        pairs = _find_overlapping_ids(schema_editor)

    if pairs:
        logger.critical(
            'TimeSlot overlap exclusion constraint НЕ создан: остались %s '
            'пересечений с занятыми/blocked слотами: %s. Разберите вручную и '
            'выполните SQL: ALTER TABLE teachers_timeslot ADD CONSTRAINT %s '
            'EXCLUDE USING gist (teacher_id WITH =, '
            'tstzrange(start_at, end_at) WITH &&);',
            len(pairs), pairs[:20], CONSTRAINT,
        )
        return

    schema_editor.execute(
        f'ALTER TABLE teachers_timeslot ADD CONSTRAINT {CONSTRAINT} '
        f'EXCLUDE USING gist (teacher_id WITH =, '
        f'tstzrange(start_at, end_at) WITH &&)'
    )


def drop_exclusion_constraint(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        f'ALTER TABLE teachers_timeslot DROP CONSTRAINT IF EXISTS {CONSTRAINT}'
    )


class Migration(migrations.Migration):

    dependencies = [
        ('teachers', '0047_remove_subject_teachers_su_search__7b9542_idx_and_more'),
    ]

    operations = [
        migrations.RunPython(add_exclusion_constraint, drop_exclusion_constraint),
    ]
