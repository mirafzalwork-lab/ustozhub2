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
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.bot = None
        
        if self.bot_token:
            self.bot = Bot(token=self.bot_token)
        else:
            logger.error("TELEGRAM_BOT_TOKEN не установлен!")
    
    def send_message_sync(self, telegram_id: int, text: str, reply_markup=None, parse_mode=None) -> bool:
        """Синхронная отправка сообщения"""
        logger.info(f"🔧 send_message_sync вызван для telegram_id={telegram_id}")
        
        if not self.bot:
            logger.error("❌ Telegram bot не инициализирован")
            return False
        
        if not self.bot_token:
            logger.error("❌ TELEGRAM_BOT_TOKEN пустой")
            return False
            
        logger.info(f"🤖 Bot инициализирован, токен присутствует (длина: {len(self.bot_token)})")
        
        try:
            # Создаем новый event loop если его нет
            try:
                loop = asyncio.get_event_loop()
                logger.info("🔄 Используем существующий event loop")
            except RuntimeError:
                logger.info("🔄 Создаем новый event loop")
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Выполняем асинхронную функцию
            logger.info(f"⏳ Начинаем асинхронную отправку сообщения пользователю {telegram_id}")
            result = loop.run_until_complete(
                self._send_message_async(telegram_id, text, reply_markup, parse_mode)
            )
            logger.info(f"✅ Асинхронная отправка завершена с результатом: {result}")
            return result
        except Exception as e:
            logger.error(f"💥 Критическая ошибка в send_message_sync: {e}", exc_info=True)
            return False
    
    async def _send_message_async(self, telegram_id: int, text: str, reply_markup=None, parse_mode=None) -> bool:
        """Асинхронная отправка сообщения"""
        logger.info(f"🚀 _send_message_async начата для telegram_id={telegram_id}")
        logger.info(f"📝 Текст сообщения: {text[:100]}...")
        logger.info(f"🎛️ parse_mode: {parse_mode}")
        
        try:
            logger.info(f"📤 Вызываем bot.send_message...")
            message = await self.bot.send_message(
                chat_id=telegram_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            logger.info(f"✅ Сообщение успешно отправлено пользователю {telegram_id}, message_id: {message.message_id}")
            return True
            
        except TelegramError as e:
            logger.error(f"🔥 TelegramError: {type(e).__name__}: {str(e)}")
            # Специальная обработка для заблокированных пользователей
            if 'chat not found' in str(e).lower() or 'bot was blocked' in str(e).lower():
                logger.warning(f"🚫 Пользователь {telegram_id} заблокировал бота или удалил чат")
                # Отмечаем пользователя как неактивного
                try:
                    from .models import TelegramUser
                    TelegramUser.objects.filter(telegram_id=telegram_id).update(
                        started_bot=False, 
                        notifications_enabled=False,
                        last_interaction=timezone.now()
                    )
                    logger.info(f"🔄 Пользователь {telegram_id} помечен как неактивный")
                except Exception as db_e:
                    logger.error(f"❌ Ошибка обновления статуса пользователя: {db_e}")
            else:
                logger.error(f"❌ Неожиданная ошибка отправки сообщения пользователю {telegram_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"💥 Неожиданная ошибка в _send_message_async: {type(e).__name__}: {str(e)}", exc_info=True)
            return False
    
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
            'details': []
        }
        
        logger.info(f"📤 Начинаем отправку {stats['total']} пользователям")
        
        for tg_user in telegram_users:
            # Проверяем, что пользователь готов к получению
            if not tg_user.started_bot:
                stats['failed'] += 1
                stats['details'].append({
                    'user': f"{tg_user.first_name} (@{tg_user.telegram_username or 'нет'})",
                    'status': 'skipped',
                    'reason': 'Не запустил бота (/start)'
                })
                continue
            
            if not tg_user.notifications_enabled:
                stats['failed'] += 1
                stats['details'].append({
                    'user': f"{tg_user.first_name} (@{tg_user.telegram_username or 'нет'})",
                    'status': 'skipped',
                    'reason': 'Уведомления отключены'
                })
                continue
            
            # Отправляем сообщение
            success = self.send_message_sync(
                telegram_id=tg_user.telegram_id,
                text=message,
                parse_mode=parse_mode
            )
            
            if success:
                stats['success'] += 1
                stats['details'].append({
                    'user': f"{tg_user.first_name} (@{tg_user.telegram_username or 'нет'})",
                    'status': 'success',
                    'reason': 'Отправлено успешно'
                })
            else:
                stats['failed'] += 1
                stats['details'].append({
                    'user': f"{tg_user.first_name} (@{tg_user.telegram_username or 'нет'})",
                    'status': 'failed',
                    'reason': 'Ошибка отправки'
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

