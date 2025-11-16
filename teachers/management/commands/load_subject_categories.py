"""
Management command для загрузки начальных категорий и предметов
Использование: python manage.py load_subject_categories
"""

from django.core.management.base import BaseCommand
from teachers.models import SubjectCategory, Subject


class Command(BaseCommand):
    help = 'Загрузить начальные категории предметов и популярные предметы'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('🚀 Начинаем загрузку категорий и предметов...'))
        
        # Определяем категории
        categories_data = [
            {
                'name': 'Математика и Точные науки',
                'icon': 'fas fa-calculator',
                'color': '#3B82F6',
                'order': 1,
                'description': 'Математика, алгебра, геометрия, тригонометрия и другие точные науки'
            },
            {
                'name': 'Естественные науки',
                'icon': 'fas fa-flask',
                'color': '#10B981',
                'order': 2,
                'description': 'Физика, химия, биология и другие естественные науки'
            },
            {
                'name': 'Языки',
                'icon': 'fas fa-language',
                'color': '#F59E0B',
                'order': 3,
                'description': 'Иностранные языки, русский язык, узбекский язык и другие'
            },
            {
                'name': 'Гуманитарные науки',
                'icon': 'fas fa-book-open',
                'color': '#8B5CF6',
                'order': 4,
                'description': 'История, литература, обществознание, философия'
            },
            {
                'name': 'Программирование и IT',
                'icon': 'fas fa-laptop-code',
                'color': '#EC4899',
                'order': 5,
                'description': 'Программирование, веб-разработка, базы данных, алгоритмы'
            },
            {
                'name': 'Искусство и Творчество',
                'icon': 'fas fa-palette',
                'color': '#F97316',
                'order': 6,
                'description': 'Рисование, музыка, дизайн и другие творческие направления'
            },
            {
                'name': 'Бизнес и Экономика',
                'icon': 'fas fa-chart-line',
                'color': '#06B6D4',
                'order': 7,
                'description': 'Экономика, бухгалтерия, маркетинг, менеджмент'
            },
            {
                'name': 'Спорт и Здоровье',
                'icon': 'fas fa-dumbbell',
                'color': '#EF4444',
                'order': 8,
                'description': 'Физическая культура, спорт, здоровый образ жизни'
            },
        ]
        
        # Создаем категории
        created_categories = {}
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
            created_categories[cat_data['name']] = category
            
            if created:
                self.stdout.write(self.style.SUCCESS(f'  ✅ Создана категория: {category.name}'))
            else:
                self.stdout.write(f'  ℹ️  Категория уже существует: {category.name}')
        
        # Определяем популярные предметы с их категориями
        popular_subjects = [
            # Математика и Точные науки
            ('Математика', 'Математика и Точные науки', 'fas fa-square-root-alt', True),
            ('Алгебра', 'Математика и Точные науки', 'fas fa-superscript', True),
            ('Геометрия', 'Математика и Точные науки', 'fas fa-shapes', True),
            ('Физика', 'Естественные науки', 'fas fa-atom', True),
            ('Химия', 'Естественные науки', 'fas fa-flask', True),
            
            # Языки
            ('Английский язык', 'Языки', 'fas fa-flag-usa', True),
            ('Русский язык', 'Языки', 'fas fa-spell-check', True),
            ('Узбекский язык', 'Языки', 'fas fa-book', True),
            ('Немецкий язык', 'Языки', 'fas fa-flag', False),
            ('Французский язык', 'Языки', 'fas fa-flag', False),
            ('Турецкий язык', 'Языки', 'fas fa-flag', False),
            ('Корейский язык', 'Языки', 'fas fa-flag', False),
            ('Китайский язык', 'Языки', 'fas fa-flag', False),
            
            # Программирование и IT
            ('Python', 'Программирование и IT', 'fab fa-python', True),
            ('JavaScript', 'Программирование и IT', 'fab fa-js', False),
            ('Web-разработка', 'Программирование и IT', 'fas fa-code', False),
            ('Базы данных', 'Программирование и IT', 'fas fa-database', False),
            
            # Гуманитарные науки
            ('История', 'Гуманитарные науки', 'fas fa-landmark', False),
            ('Литература', 'Гуманитарные науки', 'fas fa-book-reader', False),
            ('Обществознание', 'Гуманитарные науки', 'fas fa-users', False),
            
            # Естественные науки
            ('Биология', 'Естественные науки', 'fas fa-dna', True),
            ('География', 'Естественные науки', 'fas fa-globe-americas', False),
            
            # Бизнес и Экономика
            ('Экономика', 'Бизнес и Экономика', 'fas fa-coins', False),
            ('Бухгалтерский учет', 'Бизнес и Экономика', 'fas fa-calculator', False),
            
            # Искусство и Творчество
            ('Рисование', 'Искусство и Творчество', 'fas fa-paint-brush', False),
            ('Музыка', 'Искусство и Творчество', 'fas fa-music', False),
            ('Графический дизайн', 'Искусство и Творчество', 'fas fa-palette', False),
        ]
        
        # Создаем или обновляем предметы
        created_count = 0
        updated_count = 0
        
        for subject_name, category_name, icon, is_popular in popular_subjects:
            category = created_categories.get(category_name)
            
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
                created_count += 1
                emoji = '⭐' if is_popular else '📚'
                self.stdout.write(self.style.SUCCESS(f'  {emoji} Создан предмет: {subject_name} ({category_name})'))
            else:
                # Обновляем категорию для существующих предметов, если она не указана
                if not subject.category and category:
                    subject.category = category
                    subject.icon = icon
                    subject.is_popular = is_popular
                    subject.save()
                    updated_count += 1
                    self.stdout.write(f'  🔄 Обновлен предмет: {subject_name}')
        
        self.stdout.write('\n' + '='*70)
        self.stdout.write(self.style.SUCCESS(f'✅ Загрузка завершена!'))
        self.stdout.write(self.style.SUCCESS(f'📁 Создано категорий: {len(created_categories)}'))
        self.stdout.write(self.style.SUCCESS(f'📚 Создано предметов: {created_count}'))
        self.stdout.write(self.style.SUCCESS(f'🔄 Обновлено предметов: {updated_count}'))
        self.stdout.write('='*70)
        
        self.stdout.write(self.style.WARNING('\n💡 Теперь вы можете:'))
        self.stdout.write('  1. Перейти в админку для управления категориями и предметами')
        self.stdout.write('  2. Добавить дополнительные предметы через админку')
        self.stdout.write('  3. Настроить популярные предметы для отображения в топе')

