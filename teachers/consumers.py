# teachers/consumers.py
"""
WebSocket Consumer для real-time чата
Интегрируется с существующей системой Conversation и Message
"""

import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from .models import Conversation, Message, User

# Логирование для отладки
logger = logging.getLogger(__name__)


class ChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket Consumer для чата между двумя пользователями
    Интегрируется с существующей системой Conversation
    """
    
    async def connect(self):
        """
        Подключение клиента к WebSocket
        """
        # Получаем conversation_id из URL (UUID формат)
        self.conversation_id = self.scope['url_route']['kwargs']['conversation_id']
        self.room_group_name = f'chat_{self.conversation_id}'
        
        # Проверяем авторизацию пользователя
        if self.scope["user"] == AnonymousUser():
            await self.close()
            return
        
        # Проверяем доступ к конверсации
        has_access = await self.check_conversation_access()
        if not has_access:
            await self.close()
            return
        
        # Присоединяемся к группе комнаты
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        # Принимаем WebSocket соединение
        await self.accept()
        
        # Отправляем историю сообщений при подключении
        await self.send_message_history()
        
        logger.info(f"User {self.scope['user'].username} connected to conversation {self.conversation_id}")

    async def disconnect(self, close_code):
        """
        Отключение клиента от WebSocket
        """
        # Покидаем группу комнаты
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
        
        logger.info(f"User {self.scope['user'].username} disconnected from conversation {self.conversation_id}")

    async def receive(self, text_data):
        """
        Получение сообщения от клиента
        Обрабатывает разные типы сообщений
        """
        try:
            text_data_json = json.loads(text_data)
            message_type = text_data_json.get('type', 'chat_message')
            
            if message_type == 'ping':
                # Отвечаем на ping сообщением pong для поддержания соединения
                await self.send(text_data=json.dumps({
                    'type': 'pong'
                }))
                return
                
            elif message_type == 'chat_message':
                message = text_data_json.get('message', '').strip()
                
                if message:  # Отправляем только непустые сообщения
                    # Сохраняем сообщение в БД
                    chat_message = await self.save_message(message)
                    
                    # Отправляем сообщение всем участникам комнаты
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            'type': 'chat_message',
                            'message_data': await self.message_to_dict(chat_message)
                        }
                    )
            
            elif message_type == 'mark_as_read':
                # Помечаем сообщения как прочитанные
                await self.mark_messages_as_read()
                
        except json.JSONDecodeError:
            # Игнорируем некорректные JSON сообщения
            logger.error(f"Invalid JSON received from {self.scope['user'].username}")
        except Exception as e:
            logger.error(f"Error in receive: {e}")

    async def chat_message(self, event):
        """
        Отправка сообщения клиенту
        Вызывается из group_send
        """
        message_data = event['message_data']
        
        # Отправляем сообщение WebSocket клиенту
        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            'message_data': message_data
        }))

    @database_sync_to_async
    def check_conversation_access(self):
        """
        Проверяет доступ к существующей конверсации
        """
        try:
            conversation = Conversation.objects.get(id=self.conversation_id)
            # Проверяем, что пользователь участник конверсации
            return self.scope["user"] in [conversation.teacher.user, conversation.student]
        except Conversation.DoesNotExist:
            return False

    @database_sync_to_async
    def save_message(self, message_text):
        """
        Сохраняет сообщение в существующую систему
        """
        try:
            conversation = Conversation.objects.get(id=self.conversation_id)
            message = Message.objects.create(
                conversation=conversation,
                sender=self.scope["user"],
                content=message_text
            )
            # Обновляем время последнего сообщения в конверсации
            conversation.save()  # Это обновит updated_at
            return message
        except Conversation.DoesNotExist:
            return None

    @database_sync_to_async
    def message_to_dict(self, message):
        """
        Конвертирует Message в словарь для JSON
        """
        return {
            'id': message.id,
            'message': message.content,
            'sender_id': message.sender.id,
            'sender_name': message.sender.get_full_name() or message.sender.username,
            'created_at': message.created_at.isoformat(),
            'is_read': message.is_read,
        }

    @database_sync_to_async
    def get_message_history(self):
        """
        Получает историю сообщений из существующей системы
        """
        try:
            conversation = Conversation.objects.get(id=self.conversation_id)
            messages = Message.objects.filter(conversation=conversation).order_by('-created_at')[:50]
            return [self.message_to_dict_sync(msg) for msg in reversed(messages)]
        except Conversation.DoesNotExist:
            return []
    
    def message_to_dict_sync(self, message):
        """Синхронная версия message_to_dict для использования в get_message_history"""
        return {
            'id': message.id,
            'message': message.content,
            'sender_id': message.sender.id,
            'sender_name': message.sender.get_full_name() or message.sender.username,
            'created_at': message.created_at.isoformat(),
            'is_read': message.is_read,
        }

    @database_sync_to_async
    def mark_messages_as_read(self):
        """
        Помечает сообщения как прочитанные в существующей системе
        """
        try:
            conversation = Conversation.objects.get(id=self.conversation_id)
            Message.objects.filter(
                conversation=conversation,
                is_read=False
            ).exclude(sender=self.scope["user"]).update(is_read=True)
        except Conversation.DoesNotExist:
            pass

    async def send_message_history(self):
        """
        Отправляет историю сообщений при подключении
        """
        messages = await self.get_message_history()
        await self.send(text_data=json.dumps({
            'type': 'message_history',
            'messages': messages
        }))