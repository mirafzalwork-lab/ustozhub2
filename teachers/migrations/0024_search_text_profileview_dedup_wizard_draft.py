"""
Миграция:
  • Subject.search_text + индекс
  • TeacherProfile.search_text + индекс
  • ProfileView.viewed_date / views_count / last_viewed_at + дедуп существующих записей
  • WizardDraft (новая модель для сохранения черновиков регистрации)

Бэкфилл search_text и дедупликация ProfileView выполняются в RunPython.
"""

from django.db import migrations, models
import django.utils.timezone
import re


def _normalize(*parts):
    cleaned = []
    for p in parts:
        if not p:
            continue
        s = str(p).strip().lower()
        if s:
            cleaned.append(s)
    return re.sub(r'\s+', ' ', ' '.join(cleaned))


def backfill_subject_search_text(apps, schema_editor):
    Subject = apps.get_model('teachers', 'Subject')
    for subject in Subject.objects.all().only('id', 'name', 'description'):
        subject.search_text = _normalize(subject.name, subject.description)
        subject.save(update_fields=['search_text'])


def backfill_teacher_search_text(apps, schema_editor):
    TeacherProfile = apps.get_model('teachers', 'TeacherProfile')
    qs = TeacherProfile.objects.select_related('user').only(
        'id', 'bio', 'university', 'specialization',
        'user__first_name', 'user__last_name',
    )
    for tp in qs:
        tp.search_text = _normalize(
            tp.user.first_name, tp.user.last_name,
            tp.bio, tp.university, tp.specialization,
        )
        tp.save(update_fields=['search_text'])


def dedup_profile_views(apps, schema_editor):
    """
    Агрегирует существующие ProfileView по (профиль, viewer_user|viewer_ip, viewed_date).
    Оставляет одну строку с views_count = количество дублей,
    last_viewed_at = максимальный viewed_at в группе.
    """
    ProfileView = apps.get_model('teachers', 'ProfileView')

    # Сначала заполним viewed_date у всех записей (дата от viewed_at)
    # Django при добавлении поля с default=timezone.now поставит "сейчас" —
    # это неправильно для исторических данных, перезаписываем.
    for pv in ProfileView.objects.all().only('id', 'viewed_at'):
        pv.viewed_date = pv.viewed_at.date()
        pv.last_viewed_at = pv.viewed_at
        pv.save(update_fields=['viewed_date', 'last_viewed_at'])

    # Группируем
    groups = {}  # key -> list[pv]
    for pv in ProfileView.objects.all().only(
        'id', 'teacher_profile_id', 'student_profile_id',
        'viewer_user_id', 'viewer_ip', 'viewed_date', 'viewed_at',
    ):
        key = (
            pv.teacher_profile_id,
            pv.student_profile_id,
            pv.viewer_user_id,
            pv.viewer_ip if pv.viewer_user_id is None else None,
            pv.viewed_date,
        )
        groups.setdefault(key, []).append(pv)

    # Для каждой группы: оставить первую, сложить counts, удалить остальные
    to_delete_ids = []
    for items in groups.values():
        if len(items) <= 1:
            continue
        keep = items[0]
        max_viewed_at = max((i.viewed_at for i in items), default=keep.viewed_at)
        ProfileView.objects.filter(pk=keep.pk).update(
            views_count=len(items),
            last_viewed_at=max_viewed_at,
        )
        to_delete_ids.extend(i.id for i in items[1:])

    # Удаляем дубликаты пачками
    if to_delete_ids:
        ProfileView.objects.filter(id__in=to_delete_ids).delete()


def noop_reverse(apps, schema_editor):
    """Обратная миграция данных не имеет смысла — дубликаты не восстановить."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('teachers', '0023_seed_daily_reminders'),
    ]

    operations = [
        # --- Subject.search_text ---
        migrations.AddField(
            model_name='subject',
            name='search_text',
            field=models.TextField(
                blank=True, default='',
                help_text='Нормализованный текст для быстрого поиска (lowercase: name + description)'
            ),
        ),
        migrations.AddIndex(
            model_name='subject',
            index=models.Index(fields=['search_text'], name='teachers_su_search__7b9542_idx'),
        ),
        migrations.RunPython(backfill_subject_search_text, noop_reverse),

        # --- TeacherProfile.search_text ---
        migrations.AddField(
            model_name='teacherprofile',
            name='search_text',
            field=models.TextField(
                blank=True, default='',
                help_text='Нормализованный текст для быстрого поиска (заполняется автоматически)'
            ),
        ),
        migrations.AddIndex(
            model_name='teacherprofile',
            index=models.Index(fields=['search_text'], name='teachers_te_search__6fc140_idx'),
        ),
        migrations.RunPython(backfill_teacher_search_text, noop_reverse),

        # --- ProfileView дедуп ---
        migrations.AddField(
            model_name='profileview',
            name='viewed_date',
            field=models.DateField(
                default=django.utils.timezone.now, db_index=True,
                verbose_name='Дата просмотра'
            ),
        ),
        migrations.AddField(
            model_name='profileview',
            name='views_count',
            field=models.PositiveIntegerField(default=1, verbose_name='Количество просмотров в этот день'),
        ),
        migrations.AddField(
            model_name='profileview',
            name='last_viewed_at',
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                verbose_name='Время последнего просмотра в этот день'
            ),
        ),
        migrations.RunPython(dedup_profile_views, noop_reverse),
        migrations.AddIndex(
            model_name='profileview',
            index=models.Index(fields=['teacher_profile', '-viewed_date'], name='teachers_pr_teacher_06a9ec_idx'),
        ),
        migrations.AddIndex(
            model_name='profileview',
            index=models.Index(fields=['student_profile', '-viewed_date'], name='teachers_pr_student_b1ff2f_idx'),
        ),

        # --- WizardDraft ---
        migrations.CreateModel(
            name='WizardDraft',
            fields=[
                ('session_key', models.CharField(max_length=64, primary_key=True, serialize=False, verbose_name='Ключ сессии Django')),
                ('wizard_name', models.CharField(default='teacher_registration', help_text='Идентификатор wizard (на случай если их будет несколько)', max_length=50)),
                ('current_step', models.CharField(blank=True, help_text='На каком шаге был пользователь', max_length=50)),
                ('data', models.JSONField(blank=True, default=dict, help_text='Сериализованные данные wizard (storage.data)')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Черновик регистрации',
                'verbose_name_plural': 'Черновики регистрации',
                'ordering': ['-updated_at'],
                'indexes': [models.Index(fields=['-updated_at'], name='teachers_wi_updated_f467b9_idx')],
            },
        ),
    ]
