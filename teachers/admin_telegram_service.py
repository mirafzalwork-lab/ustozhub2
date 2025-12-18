"""
Сервис для отправки сообщений через админ панель
Отдельный от системы уведомлений для избежания конфликтов
"""

import logging
import asyncio
from typing import List, Dict, Optional
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from django.conf import settings
from teachers.models import TelegramUser

User = get_user_model()
logger = logging.getLogger(__name__)


class AdminTelegramService:
    """Сервис для отправки сообщений через админ панель"""
    
    def __init__(self):
        try:
            self.bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', None)
            self.bot = None
            
            if self.bot_token:
                try:
                    self.bot = Bot(token=self.bot_token)
                    logger.info(f"✅ Telegram bot инициализирован успешно")
                except Exception as e:
                    logger.error(f"❌ Ошибка создания Telegram bot: {e}")
            else:
                logger.error("❌ TELEGRAM_BOT_TOKEN не установлен в настройках!")
        except Exception as e:
            logger.error(f"❌ Критическая ошибка инициализации AdminTelegramService: {e}")
    
    def send_message_sync(self, telegram_id: int, text: str, reply_markup=None, parse_mode=None) -> dict:
        """
        Синхронная отправка сообщения с детальной информацией об ошибках
        
        Returns:
            dict: {'success': bool, 'error_type': str, 'error_message': str}
        """
        if not self.bot:
            logger.error("Telegram bot не инициализирован")
            return {
                'success': False, 
                'error_type': 'bot_not_initialized', 
                'error_message': 'Telegram bot не инициализирован'
            }
        
        try:
            # Создаем новый event loop если его нет
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Выполняем асинхронную функцию
            return loop.run_until_complete(
                self._send_message_async(telegram_id, text, reply_markup, parse_mode)
            )
        except Exception as e:
            logger.error(f"Ошибка в send_message_sync: {e}")
            return {
                'success': False, 
                'error_type': 'system_error', 
                'error_message': str(e)
            }
    
    async def _send_message_async(self, telegram_id: int, text: str, reply_markup=None, parse_mode=None) -> dict:
        """
        Асинхронная отправка сообщения с детальной обработкой ошибок
        
        Returns:
            dict: {'success': bool, 'error_type': str, 'error_message': str}
        """
        try:
            await self.bot.send_message(
                chat_id=telegram_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            logger.info(f"✅ Сообщение отправлено пользователю {telegram_id}")
            return {'success': True, 'error_type': None, 'error_message': None}
            
        except TelegramError as e:
            error_str = str(e).lower()
            
            # Детальная классификация ошибок
            if 'chat not found' in error_str:
                error_type = 'chat_not_found'
                error_message = 'Чат не найден (пользователь удалил чат с ботом)'
                
            elif 'bot was blocked' in error_str or 'forbidden' in error_str:
                error_type = 'bot_blocked'  
                error_message = 'Пользователь заблокировал бота'
                
            elif 'user is deactivated' in error_str:
                error_type = 'user_deactivated'
                error_message = 'Аккаунт пользователя деактивирован'
                
            elif 'chat_id is empty' in error_str:
                error_type = 'invalid_chat_id'
                error_message = 'Неверный ID чата'
                
            elif 'too many requests' in error_str or 'retry after' in error_str:
                error_type = 'rate_limit'
                error_message = 'Превышен лимит запросов к API Telegram'
                
            elif 'network' in error_str or 'timeout' in error_str:
                error_type = 'network_error'
                error_message = 'Ошибка сети или таймаут'
                
            else:
                error_type = 'other_telegram_error'
                error_message = f'Ошибка Telegram API: {str(e)}'
            
            logger.warning(f"⚠️ Ошибка отправки пользователю {telegram_id}: {error_message}")
            
            return {
                'success': False, 
                'error_type': error_type, 
                'error_message': error_message
            }
            
        except Exception as e:
            logger.error(f"❌ Системная ошибка при отправке пользователю {telegram_id}: {e}")
            return {
                'success': False, 
                'error_type': 'system_error', 
                'error_message': f'Системная ошибка: {str(e)}'
            }
    
    def _deactivate_user(self, telegram_id: int):
        """Деактивирует пользователя в базе данных (безопасно для async)"""
        try:
            from django.db import transaction
            from .models import TelegramUser
            
            # Выполняем в отдельном потоке для избежания проблем с async
            import threading
            
            def update_user():
                with transaction.atomic():
                    TelegramUser.objects.filter(telegram_id=telegram_id).update(
                        started_bot=False, 
                        notifications_enabled=False,
                        last_interaction=timezone.now()
                    )
            
            # Запускаем в отдельном потоке
            thread = threading.Thread(target=update_user)
            thread.start()
            thread.join(timeout=5)  # Ждем максимум 5 секунд
            
            logger.info(f"🔄 Пользователь {telegram_id} помечен для деактивации")
        except Exception as e:
            logger.error(f"Ошибка деактивации пользователя {telegram_id}: {e}")
    
    def send_to_selected_users(self, telegram_users: List[TelegramUser], message: str, parse_mode='Markdown') -> Dict[str, int]:
        """
        Отправить сообщение выбранным пользователям
        
        Args:
            telegram_users: Список TelegramUser объектов
            message: Текст сообщения
            
        Returns:
            dict: Статистика отправки
        """
        stats = {
            'success': 0,
            'failed': 0,
            'total': len(telegram_users),
            'details': [],
            'error_summary': {
                'bot_blocked': 0,
                'chat_not_found': 0,
                'user_deactivated': 0,
                'rate_limit': 0,
                'network_error': 0,
                'not_started_bot': 0,
                'notifications_disabled': 0,
                'other': 0
            }
        }
        
        logger.info(f"📤 Начинаем отправку {stats['total']} пользователям")
        
        for tg_user in telegram_users:
            # Проверяем, что пользователь готов к получению
            if not tg_user.started_bot:
                stats['failed'] += 1
                stats['error_summary']['not_started_bot'] += 1
                stats['details'].append({
                    'user': f"{tg_user.first_name} (@{tg_user.telegram_username or 'нет'})",
                    'status': 'skipped',
                    'reason': 'Не запустил бота (/start)'
                })
                continue
            
            if not tg_user.notifications_enabled:
                stats['failed'] += 1
                stats['error_summary']['notifications_disabled'] += 1
                stats['details'].append({
                    'user': f"{tg_user.first_name} (@{tg_user.telegram_username or 'нет'})",
                    'status': 'skipped',
                    'reason': 'Уведомления отключены'
                })
                continue
            
            # Отправляем сообщение
            result = self.send_message_sync(
                telegram_id=tg_user.telegram_id,
                text=message,
                parse_mode=parse_mode
            )
            
            if result['success']:
                stats['success'] += 1
                stats['details'].append({
                    'user': f"{tg_user.first_name} (@{tg_user.telegram_username or 'нет'})",
                    'status': 'success',
                    'reason': 'Отправлено успешно'
                })
            else:
                stats['failed'] += 1
                
                # Подсчитываем типы ошибок
                error_type = result['error_type']
                if error_type in stats['error_summary']:
                    stats['error_summary'][error_type] += 1
                else:
                    stats['error_summary']['other'] += 1
                
                stats['details'].append({
                    'user': f"{tg_user.first_name} (@{tg_user.telegram_username or 'нет'})",
                    'status': 'failed',
                    'reason': result['error_message'],
                    'error_type': error_type
                })
        
        logger.info(f"📊 Результаты отправки: ✅ {stats['success']}, ❌ {stats['failed']}, 📊 {stats['total']}")
        return stats
    
    def send_to_all_started_users(self, message: str, user_type: Optional[str] = None) -> Dict[str, int]:
        """
        Отправить сообщение всем пользователям, которые нажали /start
        
        Args:
            message: Текст сообщения
            user_type: Фильтр по типу пользователя ('teacher', 'student', None для всех)
            
        Returns:
            dict: Статистика отправки
        """
        # Получаем всех пользователей, которые запустили бота
        queryset = TelegramUser.objects.filter(
            started_bot=True,
            notifications_enabled=True
        )
        
        # Применяем фильтр по типу пользователя если указан
        if user_type:
            queryset = queryset.filter(user__user_type=user_type)
        
        users = list(queryset)
        
        logger.info(f"📤 Начинаем массовую рассылку для {len(users)} пользователей")
        
        return self.send_to_selected_users(users, message)
    
    def send_to_django_user(self, django_user: User, message: str) -> bool:
        """
        Отправить сообщение Django пользователю
        
        Args:
            django_user: Django User объект
            message: Текст сообщения
            
        Returns:
            bool: True если сообщение отправлено
        """
        # Сначала ищем привязанного Telegram пользователя
        telegram_user = TelegramUser.objects.filter(
            user=django_user,
            started_bot=True,
            notifications_enabled=True
        ).first()
        
        # Если не найден привязанный, ищем среди непривязанных
        if not telegram_user:
            telegram_user = TelegramUser.objects.filter(
                started_bot=True,
                notifications_enabled=True,
                user__isnull=True
            ).filter(
                models.Q(telegram_username__icontains=django_user.username) |
                models.Q(first_name__icontains=django_user.first_name) |
                models.Q(last_name__icontains=django_user.last_name)
            ).first()
        
        if not telegram_user:
            logger.warning(f"❌ Не найден Telegram пользователь для Django пользователя {django_user.username}")
            return False
        
        return self.send_message_sync(
            telegram_id=telegram_user.telegram_id,
            text=message
        )
    
    def get_ready_users_count(self, user_type: Optional[str] = None) -> int:
        """Получить количество пользователей, готовых к получению сообщений"""
        queryset = TelegramUser.objects.filter(
            started_bot=True,
            notifications_enabled=True
        )
        
        if user_type:
            queryset = queryset.filter(user__user_type=user_type)
        
        return queryset.count()
    
    def get_user_status_info(self) -> Dict[str, int]:
        """Получить информацию о статусе пользователей"""
        total_users = TelegramUser.objects.count()
        started_bot = TelegramUser.objects.filter(started_bot=True).count()
        notifications_enabled = TelegramUser.objects.filter(notifications_enabled=True).count()
        ready_users = TelegramUser.objects.filter(
            started_bot=True,
            notifications_enabled=True
        ).count()
        
        return {
            'total': total_users,
            'started_bot': started_bot,
            'notifications_enabled': notifications_enabled,
            'ready': ready_users
        }


# Создаем глобальный экземпляр сервиса
admin_telegram_service = AdminTelegramService()

