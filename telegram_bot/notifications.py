"""
Модуль для отправки уведомлений пользователям через Telegram
"""

import logging
import asyncio
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from django.conf import settings
from django.db import models
from teachers.models import TelegramUser, User

logger = logging.getLogger(__name__)


class TelegramNotificationService:
    """Сервис для отправки уведомлений в Telegram"""
    
    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.bot = None
        
        if self.bot_token:
            self.bot = Bot(token=self.bot_token)
        else:
            logger.warning("TELEGRAM_BOT_TOKEN не установлен!")
    
    async def send_message(
        self,
        telegram_id: int,
        text: str,
        reply_markup=None,
        parse_mode='Markdown'
    ) -> bool:
        """
        Отправить сообщение пользователю
        
        Args:
            telegram_id: ID пользователя в Telegram
            text: Текст сообщения
            reply_markup: Клавиатура (опционально)
            parse_mode: Режим парсинга (Markdown или HTML)
            
        Returns:
            bool: True если сообщение отправлено успешно
        """
        if not self.bot:
            logger.error("Telegram bot не инициализирован")
            return False
        
        try:
            await self.bot.send_message(
                chat_id=telegram_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            logger.info(f"Сообщение отправлено пользователю {telegram_id}")
            return True
            
        except TelegramError as e:
            logger.error(f"Ошибка отправки сообщения пользователю {telegram_id}: {e}")
            return False
    
    def send_message_sync(self, telegram_id: int, text: str, reply_markup=None, parse_mode='Markdown') -> bool:
        """
        Синхронная версия send_message для вызова из Django views/signals
        """
        try:
            # Создаем новый event loop если его нет
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Выполняем асинхронную функцию
            return loop.run_until_complete(
                self.send_message(telegram_id, text, reply_markup, parse_mode)
            )
        except Exception as e:
            logger.error(f"Ошибка в send_message_sync: {e}")
            return False
    
    def _prepare_message_notification(self, recipient_user: User, sender_name: str, message_preview: str = ""):
        """
        Prepare notification data for a new message.
        Returns (telegram_user, notification_text, reply_markup) or (None, None, None).
        """
        # Сначала ищем привязанного пользователя
        telegram_user = TelegramUser.objects.filter(
            user=recipient_user,
            notifications_enabled=True,
            started_bot=True
        ).first()

        # Если не найден привязанный, ищем по email/username среди непривязанных
        if not telegram_user:
            telegram_user = TelegramUser.objects.filter(
                notifications_enabled=True,
                started_bot=True,
                user__isnull=True
            ).filter(
                models.Q(telegram_username__icontains=recipient_user.username) |
                models.Q(first_name__icontains=recipient_user.first_name) |
                models.Q(last_name__icontains=recipient_user.last_name)
            ).first()

        if not telegram_user:
            logger.info(f"User {recipient_user.username} has no Telegram linked or notifications disabled")
            return None, None, None

        notification_text = (
            f"💬 **Новое сообщение!**\n\n"
            f"От: **{sender_name}**\n"
        )

        if message_preview:
            preview = message_preview[:100] + "..." if len(message_preview) > 100 else message_preview
            notification_text += f"\n_{preview}_\n"

        notification_text += f"\n👉 Войдите на сайт, чтобы прочитать и ответить!"

        keyboard = [[InlineKeyboardButton(
            "📬 Открыть сообщения",
            url=settings.SITE_URL
        )]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return telegram_user, notification_text, reply_markup

    async def notify_new_message(
        self,
        recipient_user: User,
        sender_name: str,
        message_preview: str = ""
    ) -> bool:
        """Уведомить пользователя о новом сообщении (async)"""
        try:
            telegram_user, text, markup = self._prepare_message_notification(
                recipient_user, sender_name, message_preview
            )
            if not telegram_user:
                return False

            return await self.send_message(
                telegram_id=telegram_user.telegram_id,
                text=text,
                reply_markup=markup
            )

        except Exception as e:
            logger.error(f"Error in notify_new_message: {e}", exc_info=True)
            return False

    def notify_new_message_sync(self, recipient_user: User, sender_name: str, message_preview: str = "") -> bool:
        """Уведомить пользователя о новом сообщении (sync)"""
        try:
            telegram_user, text, markup = self._prepare_message_notification(
                recipient_user, sender_name, message_preview
            )
            if not telegram_user:
                return False

            return self.send_message_sync(
                telegram_id=telegram_user.telegram_id,
                text=text,
                reply_markup=markup
            )

        except Exception as e:
            logger.error(f"Error in notify_new_message_sync: {e}", exc_info=True)
            return False
    
    async def send_broadcast(self, text: str, user_filter: Optional[dict] = None) -> dict:
        """
        Отправить массовую рассылку
        
        Args:
            text: Текст сообщения
            user_filter: Фильтр для пользователей (например, {'started_bot': True})
            
        Returns:
            dict: Статистика отправки {'success': int, 'failed': int, 'total': int}
        """
        # Получаем список пользователей
        queryset = TelegramUser.objects.filter(
            notifications_enabled=True,
            started_bot=True
        )
        
        if user_filter:
            queryset = queryset.filter(**user_filter)
        
        users = list(queryset.values_list('telegram_id', flat=True))
        
        stats = {
            'success': 0,
            'failed': 0,
            'total': len(users)
        }
        
        logger.info(f"Начинаем рассылку для {stats['total']} пользователей")
        
        # Отправляем сообщения
        for telegram_id in users:
            success = await self.send_message(telegram_id, text)
            if success:
                stats['success'] += 1
            else:
                stats['failed'] += 1
            
            # Небольшая задержка чтобы избежать лимитов Telegram
            await asyncio.sleep(0.05)
        
        logger.info(f"Рассылка завершена: {stats}")
        return stats
    
    def send_broadcast_sync(self, text: str, user_filter: Optional[dict] = None) -> dict:
        """Синхронная версия send_broadcast"""
        try:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            return loop.run_until_complete(
                self.send_broadcast(text, user_filter)
            )
        except Exception as e:
            logger.error(f"Ошибка в send_broadcast_sync: {e}")
            return {'success': 0, 'failed': 0, 'total': 0}


# Создаем глобальный экземпляр сервиса
notification_service = TelegramNotificationService()


# Удобные функции для быстрого использования
def send_telegram_notification(user: User, sender_name: str, message_preview: str = "") -> bool:
    """
    Отправить уведомление о новом сообщении (синхронная версия)
    
    Использование:
        from telegram_bot.notifications import send_telegram_notification
        send_telegram_notification(recipient_user, "Иван Иванов", "Привет, как дела?")
    """
    return notification_service.notify_new_message_sync(user, sender_name, message_preview)


def send_telegram_broadcast(text: str, user_filter: Optional[dict] = None) -> dict:
    """
    Отправить массовую рассылку (синхронная версия)
    
    Использование:
        from telegram_bot.notifications import send_telegram_broadcast
        stats = send_telegram_broadcast("Важное объявление!")
        print(f"Отправлено: {stats['success']}, Ошибок: {stats['failed']}")
    """
    return notification_service.send_broadcast_sync(text, user_filter)


