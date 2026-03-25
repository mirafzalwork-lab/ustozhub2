# teachers/consumers.py
"""
WebSocket Consumers:
  - NotificationConsumer: per-user push-уведомления и badge-обновления
  - ChatConsumer: real-time чат для Conversation
"""

import json
import logging
import re
from datetime import datetime, timedelta
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone
from .models import Conversation, Message, User

# ✅ Логирование для отладки
logger = logging.getLogger(__name__)

# ✅ Константы для rate limiting
MESSAGE_RATE_LIMIT = 5  # Максимум сообщений
MESSAGE_RATE_WINDOW = 60  # За 60 секунд
MIN_MESSAGE_LENGTH = 1
MAX_MESSAGE_LENGTH = 5000


# =============================================================================
# Хелпер: отправка push-уведомления пользователю через channel layer
# =============================================================================

def notify_user(user_id, event_type, payload=None):
    """
    Отправляет real-time событие конкретному пользователю через WebSocket.
    Безопасно вызывать из любого sync-контекста (views, signals, management commands).

    Args:
        user_id: ID пользователя-получателя
        event_type: тип события ('new_message', 'new_notification', 'badge_update')
        payload: dict с дополнительными данными
    """
    try:
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        group_name = f'notifications_{user_id}'
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                'type': 'push_notification',
                'event_type': event_type,
                'payload': payload or {},
            }
        )
    except Exception as e:
        logger.warning(f"notify_user failed for user_id={user_id}: {e}")


class NotificationConsumer(AsyncWebsocketConsumer):
    """
    Per-user WebSocket для push-уведомлений.
    Каждый залогиненный пользователь подключается к своей группе notifications_{user_id}.
    Получает события: new_message, new_notification, badge_update.
    """

    async def connect(self):
        self.user = self.scope.get('user')
        if not self.user or isinstance(self.user, AnonymousUser):
            await self.close(code=4001)
            return

        self.group_name = f'notifications_{self.user.pk}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Сразу отправляем текущие badge-счётчики при подключении
        counts = await self.get_badge_counts()
        await self.send(text_data=json.dumps({
            'type': 'badge_update',
            'payload': counts,
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        """Клиент может запросить актуальные badge"""
        try:
            data = json.loads(text_data)
        except (json.JSONDecodeError, TypeError):
            return
        if data.get('type') == 'ping':
            await self.send(text_data=json.dumps({'type': 'pong'}))
        elif data.get('type') == 'get_badges':
            counts = await self.get_badge_counts()
            await self.send(text_data=json.dumps({
                'type': 'badge_update',
                'payload': counts,
            }))

    async def push_notification(self, event):
        """Обработчик group_send событий — пересылает клиенту"""
        await self.send(text_data=json.dumps({
            'type': event.get('event_type', 'badge_update'),
            'payload': event.get('payload', {}),
        }))

    @database_sync_to_async
    def get_badge_counts(self):
        """Получает актуальные badge-счётчики из БД"""
        from .context_processors import _get_user_conversations
        from .models import Notification

        user = self.user
        # Unread messages
        conversations = _get_user_conversations(user)
        conv_ids = list(conversations.values_list('id', flat=True))
        unread_messages = 0
        if conv_ids:
            unread_messages = Message.objects.filter(
                conversation_id__in=conv_ids,
                is_read=False
            ).exclude(sender=user).count()

        # Unread notifications
        unread_notifications = Notification.get_unread_count(user)

        return {
            'unread_messages': unread_messages,
            'unread_notifications': unread_notifications,
        }


# =============================================================================
# ChatConsumer: real-time чат для Conversation
# =============================================================================

class ChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket Consumer для чата между двумя пользователями
    Интегрируется с существующей системой Conversation
    """
    
    async def connect(self):
        """
        ✅ Подключение клиента к WebSocket с обработкой исключений
        """
        try:
            # Получаем conversation_id из URL (UUID формат)
            self.conversation_id = self.scope['url_route']['kwargs']['conversation_id']
            self.room_group_name = f'chat_{self.conversation_id}'
            self.user = self.scope["user"]
            self.conversation = None
            
            # ✅ Проверяем авторизацию пользователя
            if isinstance(self.user, AnonymousUser):
                logger.warning(f"Unauthorized connection attempt to {self.conversation_id}")
                await self.close(code=4001)
                return
            
            # ✅ Проверяем доступ к конверсации
            has_access = await self.check_conversation_access()
            if not has_access:
                logger.warning(
                    f"Access denied for user_id={self.user.pk} to conversation {self.conversation_id}"
                )
                await self.close(code=4003)
                return
            
            # ✅ Присоединяемся к группе комнаты
            await self.channel_layer.group_add(
                self.room_group_name,
                self.channel_name
            )
            
            # ✅ Принимаем WebSocket соединение
            await self.accept()
            
            # ✅ Отправляем историю сообщений при подключении
            await self.send_message_history()
            
            logger.info(
                f"✅ User user_id={self.user.pk} ({self.user.username}) connected to "
                f"conversation {self.conversation_id}"
            )
        
        except KeyError as e:
            logger.error(f"Missing URL parameter in chat connection: {e}", exc_info=True)
            await self.close(code=4000)
        except Exception as e:
            logger.error(f"Error in chat connect: {e}", exc_info=True)
            await self.close(code=4500)

    async def disconnect(self, close_code):
        """
        ✅ Отключение клиента от WebSocket с логированием
        """
        try:
            # Покидаем группу комнаты
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )
            
            logger.info(
                f"User user_id={self.user.pk} ({self.user.username}) disconnected from "
                f"conversation {self.conversation_id} (code: {close_code})"
            )
        except Exception as e:
            logger.error(f"Error in chat disconnect: {e}", exc_info=True)

    async def receive(self, text_data):
        """
        ✅ Получение сообщения от клиента с валидацией и rate limiting
        """
        try:
            # ✅ Парсим JSON с обработкой ошибок
            try:
                text_data_json = json.loads(text_data)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON received from user_id={self.user.pk}: {e}")
                await self.send_error('Invalid JSON format')
                return
            
            message_type = text_data_json.get('type', 'chat_message')
            
            if message_type == 'ping':
                # ✅ Отвечаем на ping сообщением pong для поддержания соединения
                logger.debug(f"📡 Ping from user_id={self.user.pk}")
                await self.send(text_data=json.dumps({
                    'type': 'pong',
                    'timestamp': timezone.now().isoformat()
                }))
                return
                
            elif message_type == 'chat_message':
                # ✅ Получаем и валидируем сообщение
                message_text = text_data_json.get('message', '').strip()
                
                if not message_text:
                    await self.send_error('Message cannot be empty')
                    return
                
                # ✅ Проверяем длину сообщения
                if len(message_text) < MIN_MESSAGE_LENGTH or len(message_text) > MAX_MESSAGE_LENGTH:
                    await self.send_error(
                        f'Message length must be between {MIN_MESSAGE_LENGTH} and {MAX_MESSAGE_LENGTH}'
                    )
                    return
                
                # ✅ Проверяем rate limit
                is_rate_limited = await self.check_rate_limit()
                if is_rate_limited:
                    logger.warning(f"Rate limit exceeded for user_id={self.user.pk}")
                    await self.send_error('Too many messages, please wait')
                    return
                
                # ✅ Сохраняем сообщение в БД
                chat_message = await self.save_message(message_text)
                
                if not chat_message:
                    logger.error(f"Failed to save message from user_id={self.user.pk}")
                    await self.send_error('Failed to save message')
                    return
                
                # ✅ Отправляем сообщение всем участникам комнаты
                message_data = await self.message_to_dict(chat_message)
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        'type': 'chat_message',
                        'message_data': message_data
                    }
                )
            
            elif message_type == 'mark_as_read':
                # ✅ Помечаем сообщения как прочитанные с обработкой ошибок
                try:
                    await self.mark_messages_as_read()
                except Exception as e:
                    logger.error(f"Error marking messages as read: {e}")
                    await self.send_error('Failed to mark messages as read')
            
            else:
                logger.warning(f"Unknown message type: {message_type}")
                await self.send_error(f'Unknown message type: {message_type}')
                
        except Exception as e:
            logger.error(f"Error in receive for user_id={self.user.pk}: {e}", exc_info=True)
            await self.send_error('Internal server error')

    async def chat_message(self, event):
        """
        ✅ Отправка сообщения клиенту с обработкой исключений
        """
        try:
            message_data = event.get('message_data')
            
            if not message_data:
                logger.error(f"Missing message_data in chat_message event")
                return
            
            # ✅ Отправляем сообщение WebSocket клиенту
            await self.send(text_data=json.dumps({
                'type': 'chat_message',
                'message_data': message_data
            }))
        
        except Exception as e:
            logger.error(f"Error in chat_message handler for user_id={self.user.pk}: {e}", exc_info=True)

    async def send_error(self, error_message):
        """
        ✅ Отправляет клиенту сообщение об ошибке
        """
        try:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'error': error_message,
                'timestamp': timezone.now().isoformat()
            }))
        except Exception as e:
            logger.error(f"Error sending error message: {e}")

    @database_sync_to_async
    def check_conversation_access(self):
        """
        ✅ Проверяет доступ к конверсации с кэшированием
        """
        try:
            self.conversation = Conversation.objects.select_related(
                'teacher', 'student'
            ).get(id=self.conversation_id)
            
            # ✅ Проверяем, что пользователь участник конверсации
            is_teacher = (
                self.user == self.conversation.teacher.user
                if hasattr(self.conversation.teacher, 'user')
                else False
            )
            is_student = self.user == self.conversation.student
            
            return is_teacher or is_student
        
        except Conversation.DoesNotExist:
            logger.debug(f"Conversation {self.conversation_id} not found")
            return False
        except Exception as e:
            logger.error(f"Error checking conversation access: {e}", exc_info=True)
            return False

    @database_sync_to_async
    def save_message(self, message_text):
        """
        ✅ Сохраняет сообщение в БД с обработкой исключений
        """
        try:
            if not self.conversation:
                logger.error(f"Conversation not loaded for user_id={self.user.pk}")
                return None
            
            message = Message.objects.create(
                conversation=self.conversation,
                sender=self.user,
                content=message_text
            )
            
            # ✅ Обновляем время последнего сообщения в конверсации
            self.conversation.updated_at = timezone.now()
            self.conversation.save(update_fields=['updated_at'])
            
            logger.debug(f"Message created: id={message.id}, user_id={self.user.pk}")
            return message
        
        except Exception as e:
            logger.error(f"Error saving message from user_id={self.user.pk}: {e}", exc_info=True)
            return None

    @database_sync_to_async
    def message_to_dict(self, message):
        """
        ✅ Конвертирует Message в словарь для JSON
        """
        try:
            return {
                'id': str(message.id),
                'message': message.content,
                'sender_id': message.sender.id,
                'sender_name': message.sender.get_full_name() or message.sender.username,
                'created_at': message.created_at.isoformat(),
                'is_read': message.is_read,
            }
        except Exception as e:
            logger.error(f"Error converting message to dict: {e}")
            return None

    @database_sync_to_async
    def get_message_history(self):
        """
        ✅ Получает историю сообщений из БД
        """
        try:
            if not self.conversation:
                return []
            
            messages = Message.objects.filter(
                conversation=self.conversation
            ).select_related('sender').order_by('-created_at')[:50]
            
            # ✅ Конвертируем в список и разворачиваем в правильном порядке
            result = []
            for msg in reversed(messages):
                msg_dict = {
                    'id': str(msg.id),
                    'message': msg.content,
                    'sender_id': msg.sender.id,
                    'sender_name': msg.sender.get_full_name() or msg.sender.username,
                    'created_at': msg.created_at.isoformat(),
                    'is_read': msg.is_read,
                }
                result.append(msg_dict)
            
            logger.debug(f"Retrieved {len(result)} messages for conversation {self.conversation_id}")
            return result
        
        except Exception as e:
            logger.error(f"Error getting message history: {e}", exc_info=True)
            return []

    @database_sync_to_async
    def mark_messages_as_read(self):
        """
        ✅ Помечает сообщения как прочитанные
        """
        try:
            if not self.conversation:
                return
            
            updated_count = Message.objects.filter(
                conversation=self.conversation,
                is_read=False
            ).exclude(
                sender=self.user
            ).update(is_read=True, read_at=timezone.now())

            # Сбрасываем кэш badge для текущего пользователя
            if updated_count > 0:
                from .context_processors import invalidate_message_cache
                invalidate_message_cache(self.user.pk)

            logger.debug(
                f"Marked {updated_count} messages as read for user_id={self.user.pk} "
                f"in conversation {self.conversation_id}"
            )
        
        except Exception as e:
            logger.error(f"Error marking messages as read: {e}", exc_info=True)

    @database_sync_to_async
    def check_rate_limit(self):
        """
        ✅ Проверяет rate limit для пользователя
        """
        try:
            # ✅ Считаем сообщения за последнюю минуту
            time_threshold = timezone.now() - timedelta(seconds=MESSAGE_RATE_WINDOW)
            
            recent_messages = Message.objects.filter(
                sender=self.user,
                created_at__gte=time_threshold
            ).count()
            
            is_limited = recent_messages >= MESSAGE_RATE_LIMIT
            
            if is_limited:
                logger.debug(
                    f"Rate limit exceeded for user_id={self.user.pk}: "
                    f"{recent_messages}/{MESSAGE_RATE_LIMIT} messages"
                )
            
            return is_limited
        
        except Exception as e:
            logger.error(f"Error checking rate limit: {e}")
            # В случае ошибки - не блокируем пользователя
            return False

    async def send_message_history(self):
        """
        ✅ Отправляет историю сообщений при подключении
        """
        try:
            messages = await self.get_message_history()
            await self.send(text_data=json.dumps({
                'type': 'message_history',
                'messages': messages,
                'count': len(messages)
            }))
        except Exception as e:
            logger.error(f"Error sending message history: {e}", exc_info=True)