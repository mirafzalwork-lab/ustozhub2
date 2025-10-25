"""
Модуль для отправки уведомлений пользователям через Telegram
"""

import logging
import asyncio
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from django.conf import settings
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
    
    async def notify_new_message(
        self, 
        recipient_user: User, 
        sender_name: str,
        message_preview: str = ""
    ) -> bool:
        """
        Уведомить пользователя о новом сообщении
        
        Args:
            recipient_user: Пользователь-получатель (Django User)
            sender_name: Имя отправителя
            message_preview: Предпросмотр сообщения (опционально)
            
        Returns:
            bool: True если уведомление отправлено
        """
        try:
            # Получаем Telegram профиль пользователя
            telegram_user = TelegramUser.objects.filter(
                user=recipient_user,
                notifications_enabled=True
            ).first()
            
            if not telegram_user:
                logger.info(f"Пользователь {recipient_user.username} не подключил Telegram или отключил уведомления")
                return False
            
            # Формируем текст уведомления
            notification_text = (
                f"💬 **Новое сообщение!**\n\n"
                f"От: **{sender_name}**\n"
            )
            
            if message_preview:
                # Обрезаем превью если слишком длинное
                preview = message_preview[:100] + "..." if len(message_preview) > 100 else message_preview
                notification_text += f"\n_{preview}_\n"
            
            notification_text += f"\n👉 Войдите на сайт, чтобы прочитать и ответить!"
            
            # Создаем кнопку для перехода на сайт
            webapp_url = f"{settings.TELEGRAM_WEBAPP_URL}/profile"
            keyboard = [[InlineKeyboardButton(
                "📬 Открыть сообщения",
                url=settings.SITE_URL  # Прямая ссылка на сайт
            )]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Отправляем уведомление
            return await self.send_message(
                telegram_id=telegram_user.telegram_id,
                text=notification_text,
                reply_markup=reply_markup
            )
            
        except Exception as e:
            logger.error(f"Ошибка в notify_new_message: {e}")
            return False
    
    def notify_new_message_sync(self, recipient_user: User, sender_name: str, message_preview: str = "") -> bool:
        """Синхронная версия notify_new_message"""
        try:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            return loop.run_until_complete(
                self.notify_new_message(recipient_user, sender_name, message_preview)
            )
        except Exception as e:
            logger.error(f"Ошибка в notify_new_message_sync: {e}")
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


