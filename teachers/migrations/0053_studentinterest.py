from django.conf import settings
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('teachers', '0052_lesson_attendance_overlap'),
    ]

    operations = [
        migrations.CreateModel(
            name='StudentInterest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('has_trial', models.BooleanField(default=False, verbose_name='Бронировал пробный')),
                ('trial_at', models.DateTimeField(blank=True, null=True, verbose_name='Время брони пробного')),
                ('has_favorite', models.BooleanField(default=False, verbose_name='В избранном')),
                ('favorited_at', models.DateTimeField(blank=True, null=True, verbose_name='Время добавления в избранное')),
                ('view_count', models.PositiveIntegerField(default=0, verbose_name='Дней с просмотром профиля')),
                ('first_viewed_at', models.DateTimeField(blank=True, null=True, verbose_name='Первый просмотр')),
                ('last_viewed_at', models.DateTimeField(blank=True, null=True, verbose_name='Последний просмотр')),
                ('temperature', models.CharField(choices=[('hot', 'Горячий (пробный урок)'), ('warm', 'Тёплый (избранное)'), ('cold', 'Холодный (просмотр профиля)')], db_index=True, default='cold', max_length=4, verbose_name='Температура')),
                ('last_activity_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now, verbose_name='Последняя активность')),
                ('opted_out_at', models.DateTimeField(blank=True, null=True, verbose_name='Отказ ученика')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='teacher_interests', to=settings.AUTH_USER_MODEL, verbose_name='Ученик')),
                ('teacher', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='student_interests', to='teachers.teacherprofile', verbose_name='Учитель')),
            ],
            options={
                'verbose_name': 'Интерес ученика',
                'verbose_name_plural': 'Интересы учеников',
            },
        ),
        migrations.AddConstraint(
            model_name='studentinterest',
            constraint=models.UniqueConstraint(fields=('teacher', 'student'), name='uniq_teacher_student_interest'),
        ),
        migrations.AddIndex(
            model_name='studentinterest',
            index=models.Index(fields=['teacher', 'opted_out_at', '-last_activity_at'], name='si_teacher_active_recent'),
        ),
        migrations.AddIndex(
            model_name='studentinterest',
            index=models.Index(fields=['teacher', 'temperature'], name='si_teacher_temp_idx'),
        ),
    ]
