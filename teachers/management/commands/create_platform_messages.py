"""
Django management команда для создания тестовых сообщений платформы
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from teachers.models import PlatformMessage

User = get_user_model()

class Command(BaseCommand):
    help = 'Создает тестовые сообщения платформы'

    def handle(self, *args, **options):
        self.stdout.write('🚀 Создание тестовых сообщений платформы...')
        
        try:
            # Получаем пользователя для создания сообщений
            admin_user = User.objects.filter(is_superuser=True).first()
            if not admin_user:
                admin_user = User.objects.filter(is_staff=True).first()
            if not admin_user:
                admin_user = User.objects.first()
            
            if not admin_user:
                self.stdout.write(
                    self.style.ERROR('❌ Не найдено ни одного пользователя! Создайте пользователя сначала.')
                )
                return
            
            # Создаем тестовые сообщения
            messages_data = [
                {
                    'title': '🎉 Добро пожаловать в UstozHub!',
                    'content': 'Добро пожаловать на нашу платформу! Здесь вы найдете лучших учителей для изучения любых предметов.',
                    'message_type': 'info',
                    'priority': 10
                },
                {
                    'title': '📢 Новые функции платформы',
                    'content': 'Мы добавили систему уведомлений и улучшили интерфейс поиска учителей. Проверьте новые возможности!',
                    'message_type': 'success',
                    'priority': 8
                },
                {
                    'title': '⚠️ Техническое обслуживание',
                    'content': 'Планируется техническое обслуживание системы 30 ноября с 02:00 до 04:00. Возможны кратковременные перебои.',
                    'message_type': 'warning',
                    'priority': 7
                }
            ]
            
            created_count = 0
            for msg_data in messages_data:
                # Проверяем, не существует ли уже такое сообщение
                if not PlatformMessage.objects.filter(title=msg_data['title']).exists():
                    message = PlatformMessage.objects.create(
                        title=msg_data['title'],
                        content=msg_data['content'],
                        message_type=msg_data['message_type'],
                        priority=msg_data['priority'],
                        is_active=True,
                        show_to_all=False,
                        show_to_guests=True,
                        show_to_teachers=True,
                        show_to_students=True,
                        created_by=admin_user
                    )
                    created_count += 1
                    self.stdout.write(f'✅ Создано: {message.title}')
                else:
                    self.stdout.write(f'⏭️  Уже существует: {msg_data["title"]}')
            
            # Статистика
            total_messages = PlatformMessage.objects.count()
            active_messages = PlatformMessage.objects.filter(is_active=True).count()
            
            self.stdout.write('')
            self.stdout.write(f'📊 Создано новых сообщений: {created_count}')
            self.stdout.write(f'📊 Всего сообщений в БД: {total_messages}')
            self.stdout.write(f'📊 Активных сообщений: {active_messages}')
            self.stdout.write('')
            self.stdout.write(
                self.style.SUCCESS('🎯 Готово! Проверьте навбар на сайте - должны появиться уведомления.')
            )
            
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Ошибка при создании сообщений: {e}')
            )