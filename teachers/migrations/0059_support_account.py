"""
Системный аккаунт «Поддержка UstozHub» для админ-чатов.

Его TeacherProfile ставится в teacher-слот Conversation, а целевой
пользователь — в student-слот, что позволяет админу вести прямой чат с любым
учеником или учителем, переиспользуя весь движок переписки. Аккаунт скрыт из
публичных листингов (профиль is_active=False, moderation_status='rejected').
Идемпотентно: повторный запуск не создаёт дублей.
"""
from django.db import migrations

SUPPORT_USERNAME = '__support__'
SUPPORT_DISPLAY_NAME = 'Поддержка UstozHub'


def create_support(apps, schema_editor):
    User = apps.get_model('teachers', 'User')
    TeacherProfile = apps.get_model('teachers', 'TeacherProfile')

    user, created = User.objects.get_or_create(
        username=SUPPORT_USERNAME,
        defaults={
            'first_name': SUPPORT_DISPLAY_NAME,
            'user_type': 'teacher',
            'is_active': True,
            'is_staff': False,
            'email': '',
        },
    )
    if created:
        # Нелогинящийся системный аккаунт — непригодный для входа пароль.
        user.password = '!'
        user.save(update_fields=['password'])

    TeacherProfile.objects.get_or_create(
        user=user,
        defaults={
            'experience_years': 0,
            'is_active': False,
            'moderation_status': 'rejected',
        },
    )


def remove_support(apps, schema_editor):
    User = apps.get_model('teachers', 'User')
    # Удаляем аккаунт вместе с профилем и беседами (CASCADE).
    User.objects.filter(username=SUPPORT_USERNAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('teachers', '0058_group_programming_subjects'),
    ]

    operations = [
        migrations.RunPython(create_support, remove_support),
    ]
