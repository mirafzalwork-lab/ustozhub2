"""
Скрипт для заполнения категорий предметов и обновления существующих предметов
Запустить: python manage.py shell < initial_categories.py
"""

from teachers.models import SubjectCategory, Subject

# Создаем категории
categories_data = [
    {
        'name': 'Точные науки',
        'icon': 'fas fa-calculator',
        'color': '#3B82F6',
        'order': 1,
        'description': 'Математика, физика, химия и другие точные науки'
    },
    {
        'name': 'Языки',
        'icon': 'fas fa-language',
        'color': '#10B981',
        'order': 2,
        'description': 'Иностранные языки: английский, русский, узбекский и другие'
    },
    {
        'name': 'IT и Программирование',
        'icon': 'fas fa-laptop-code',
        'color': '#8B5CF6',
        'order': 3,
        'description': 'Программирование, веб-разработка, дизайн'
    },
    {
        'name': 'Бизнес и Экономика',
        'icon': 'fas fa-chart-line',
        'color': '#F59E0B',
        'order': 4,
        'description': 'Экономика, маркетинг, менеджмент, финансы'
    },
    {
        'name': 'Творчество и Искусство',
        'icon': 'fas fa-palette',
        'color': '#EC4899',
        'order': 5,
        'description': 'Музыка, рисование, дизайн, фотография'
    },
    {
        'name': 'Спорт и Здоровье',
        'icon': 'fas fa-dumbbell',
        'color': '#EF4444',
        'order': 6,
        'description': 'Фитнес, йога, боевые искусства, танцы'
    },
    {
        'name': 'Естественные науки',
        'icon': 'fas fa-flask',
        'color': '#06B6D4',
        'order': 7,
        'description': 'Биология, география, экология'
    },
    {
        'name': 'Гуманитарные науки',
        'icon': 'fas fa-book-open',
        'color': '#A855F7',
        'order': 8,
        'description': 'История, литература, философия'
    },
    {
        'name': 'Другое',
        'icon': 'fas fa-ellipsis-h',
        'color': '#6B7280',
        'order': 99,
        'description': 'Остальные предметы'
    },
]

print("🚀 Начинаем создание категорий...")

created_count = 0
for cat_data in categories_data:
    category, created = SubjectCategory.objects.get_or_create(
        name=cat_data['name'],
        defaults={
            'icon': cat_data['icon'],
            'color': cat_data['color'],
            'order': cat_data['order'],
            'description': cat_data['description'],
            'is_active': True
        }
    )
    if created:
        print(f"✅ Создана категория: {category.name}")
        created_count += 1
    else:
        print(f"⚠️  Категория уже существует: {category.name}")

print(f"\n✅ Создано категорий: {created_count}/{len(categories_data)}")

# Распределение предметов по категориям
subject_mapping = {
    'Точные науки': [
        ('Математика', 'fas fa-square-root-alt', True),
        ('Физика', 'fas fa-atom', True),
        ('Химия', 'fas fa-flask', False),
        ('Алгебра', 'fas fa-calculator', False),
        ('Геометрия', 'fas fa-shapes', False),
    ],
    'Языки': [
        ('Английский язык', 'fas fa-flag-usa', True),
        ('Русский язык', 'fas fa-flag', True),
        ('Узбекский язык', 'fas fa-language', False),
        ('Турецкий язык', 'fas fa-language', False),
        ('Немецкий язык', 'fas fa-language', False),
        ('Французский язык', 'fas fa-language', False),
        ('Испанский язык', 'fas fa-language', False),
        ('Китайский язык', 'fas fa-language', False),
        ('Корейский язык', 'fas fa-language', False),
    ],
    'IT и Программирование': [
        ('Программирование', 'fas fa-code', True),
        ('Python', 'fab fa-python', True),
        ('JavaScript', 'fab fa-js', False),
        ('Веб-дизайн', 'fas fa-paint-brush', False),
        ('Графический дизайн', 'fas fa-pencil-ruler', False),
        ('Data Science', 'fas fa-chart-bar', False),
        ('Machine Learning', 'fas fa-brain', False),
        ('Mobile Development', 'fas fa-mobile-alt', False),
    ],
    'Бизнес и Экономика': [
        ('Экономика', 'fas fa-coins', False),
        ('Маркетинг', 'fas fa-bullhorn', False),
        ('Менеджмент', 'fas fa-users-cog', False),
        ('Финансы', 'fas fa-dollar-sign', False),
        ('Бухгалтерский учет', 'fas fa-file-invoice-dollar', False),
    ],
    'Творчество и Искусство': [
        ('Музыка', 'fas fa-music', False),
        ('Гитара', 'fas fa-guitar', False),
        ('Фортепиано', 'fas fa-piano', False),
        ('Вокал', 'fas fa-microphone', False),
        ('Рисование', 'fas fa-paint-brush', False),
        ('Фотография', 'fas fa-camera', False),
    ],
    'Спорт и Здоровье': [
        ('Фитнес', 'fas fa-running', False),
        ('Йога', 'fas fa-spa', False),
        ('Бокс', 'fas fa-fist-raised', False),
        ('Карате', 'fas fa-hand-rock', False),
        ('Танцы', 'fas fa-dance', False),
        ('Шахматы', 'fas fa-chess', False),
    ],
    'Естественные науки': [
        ('Биология', 'fas fa-microscope', False),
        ('География', 'fas fa-globe', False),
        ('Экология', 'fas fa-leaf', False),
    ],
    'Гуманитарные науки': [
        ('История', 'fas fa-landmark', False),
        ('Литература', 'fas fa-book', False),
        ('Философия', 'fas fa-brain', False),
        ('Психология', 'fas fa-user-md', False),
    ],
}

print("\n🔄 Обновляем предметы...")

updated_count = 0
created_subjects = 0

for category_name, subjects in subject_mapping.items():
    try:
        category = SubjectCategory.objects.get(name=category_name)
        
        for subject_name, icon, is_popular in subjects:
            subject, created = Subject.objects.get_or_create(
                name=subject_name,
                defaults={
                    'category': category,
                    'icon': icon,
                    'is_popular': is_popular,
                    'is_active': True
                }
            )
            
            if created:
                print(f"✅ Создан предмет: {subject_name} → {category_name}")
                created_subjects += 1
            else:
                # Обновляем существующий предмет
                subject.category = category
                subject.icon = icon
                if is_popular:
                    subject.is_popular = is_popular
                subject.save()
                print(f"🔄 Обновлен предмет: {subject_name} → {category_name}")
                updated_count += 1
                
    except SubjectCategory.DoesNotExist:
        print(f"❌ Категория не найдена: {category_name}")

print(f"\n✅ Создано предметов: {created_subjects}")
print(f"🔄 Обновлено предметов: {updated_count}")
print(f"\n🎉 Готово! Всего предметов в базе: {Subject.objects.count()}")
print(f"📊 Всего категорий: {SubjectCategory.objects.count()}")
