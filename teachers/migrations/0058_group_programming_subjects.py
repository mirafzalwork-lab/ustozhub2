"""
Группировка программирование-предметов под категорией «IT и Программирование».

Раньше предметы (Frontend, Python, SQL…) висели без категории (category=None),
из-за чего категория была пустой, а на главной не было блока «Категории».
Миграция идемпотентна: создаёт недостающие предметы, привязывает их к категории,
аккуратно переименовывает «python backend» → «Backend» (сохраняя учителей).
"""
from django.db import migrations

CATEGORY_NAME = 'IT и Программирование'
CATEGORY_DEFAULTS = {
    'icon': 'fas fa-laptop-code',
    'color': '#3B82F6',
    'order': 2,
    'is_active': True,
}

# Предметы категории. Существующие будут привязаны, отсутствующие — созданы
# (с 0 учителей, доступны для выбора учителями при регистрации).
PROGRAMMING_SUBJECTS = [
    'Frontend', 'Backend', 'Python', 'Java', 'JavaScript', 'TypeScript',
    'React', 'Node.js', 'SQL / Базы данных', 'C++', 'C#', 'PHP',
    'Go (Golang)', 'DevOps', 'Data Science / ML', 'Mobile App Development',
]


def group_subjects(apps, schema_editor):
    SubjectCategory = apps.get_model('teachers', 'SubjectCategory')
    Subject = apps.get_model('teachers', 'Subject')

    category, _ = SubjectCategory.objects.get_or_create(
        name=CATEGORY_NAME, defaults=CATEGORY_DEFAULTS,
    )

    # «python backend» → «Backend» (переименование сохраняет связи учителей).
    pb = Subject.objects.filter(name__iexact='python backend').first()
    if pb and not Subject.objects.filter(name__iexact='Backend').exclude(pk=pb.pk).exists():
        pb.name = 'Backend'
        pb.save(update_fields=['name'])

    for name in PROGRAMMING_SUBJECTS:
        subj = Subject.objects.filter(name__iexact=name).first()
        if subj is None:
            subj = Subject.objects.create(name=name, is_active=True)
        if subj.category_id != category.id:
            subj.category = category
            subj.save(update_fields=['category'])


def ungroup_subjects(apps, schema_editor):
    # Обратная миграция: только отвязываем предметы от категории, не удаляем их
    # (учительские связи и сами предметы должны сохраниться).
    SubjectCategory = apps.get_model('teachers', 'SubjectCategory')
    Subject = apps.get_model('teachers', 'Subject')
    category = SubjectCategory.objects.filter(name=CATEGORY_NAME).first()
    if category:
        Subject.objects.filter(category=category).update(category=None)


class Migration(migrations.Migration):

    dependencies = [
        ('teachers', '0057_message_is_admin_message'),
    ]

    operations = [
        migrations.RunPython(group_subjects, ungroup_subjects),
    ]
