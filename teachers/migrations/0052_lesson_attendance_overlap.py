import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('teachers', '0051_subject_name_en_subject_name_uz'),
    ]

    operations = [
        # 1) Переименование скаляров присутствия в *_duration_seconds (по ТЗ).
        migrations.RenameField(
            model_name='booking',
            old_name='teacher_present_seconds',
            new_name='teacher_duration_seconds',
        ),
        migrations.RenameField(
            model_name='booking',
            old_name='student_present_seconds',
            new_name='student_duration_seconds',
        ),
        # 2) Новые скаляры: последний выход каждой стороны и время overlap.
        migrations.AddField(
            model_name='booking',
            name='teacher_left_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='booking',
            name='student_left_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='booking',
            name='overlap_duration_seconds',
            field=models.PositiveIntegerField(default=0),
        ),
        # 3) Новый исход урока в журнале событий.
        migrations.AlterField(
            model_name='lessonevent',
            name='kind',
            field=models.CharField(
                choices=[
                    ('join_teacher', 'Учитель подключился'),
                    ('join_student', 'Ученик подключился'),
                    ('settle_completed', 'Урок проведён'),
                    ('settle_no_show_teacher', 'Неявка учителя'),
                    ('no_show_forgiven', 'Неявка ученика прощена'),
                    ('no_show_consumed', 'Неявка ученика — урок списан'),
                    ('settle_not_held', 'Урок не состоялся'),
                    ('settle_low_overlap', 'Урок не подтверждён: мало одновременного присутствия'),
                    ('warning_sent', 'Отправлено предупреждение'),
                    ('payout', 'Выплата учителю'),
                    ('refund', 'Возврат ученику'),
                ],
                db_index=True, max_length=32,
            ),
        ),
        # 4) Источник истины по интервалам присутствия.
        migrations.CreateModel(
            name='LessonAttendanceSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('teacher', 'Учитель'), ('student', 'Ученик')], db_index=True, max_length=8)),
                ('joined_at', models.DateTimeField()),
                ('left_at', models.DateTimeField(blank=True, null=True)),
                ('booking', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attendance_sessions', to='teachers.booking')),
            ],
            options={
                'verbose_name': 'Сессия присутствия',
                'verbose_name_plural': 'Сессии присутствия',
                'ordering': ['joined_at'],
            },
        ),
        migrations.AddIndex(
            model_name='lessonattendancesession',
            index=models.Index(fields=['booking', 'role'], name='teachers_le_booking_953dc0_idx'),
        ),
    ]
