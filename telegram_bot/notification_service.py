"""
Продвинутый сервис уведомлений через Telegram
С поддержкой rate limiting, retry логики, батчинга и идемпотентности
"""

import logging
import asyncio
import hashlib
import time
from typing import Optional, Dict, Any, List
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.conf import settings

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError, RetryAfter, TimedOut, NetworkError
from telegram.helpers import escape_markdown

from teachers.models import User, TelegramUser, NotificationQueue, NotificationLog

logger = logging.getLogger(__name__)


def build_notification_text(title: str, message: str) -> str:
    """Собирает текст уведомления для Telegram (parse_mode='Markdown').

    КРИТИЧНО для безопасности: `title`/`message` могут содержать
    пользовательский контент (превью сообщения чата, имя отправителя). Без
    экранирования валидная вредоносная Markdown-разметка проходит fallback
    «can't parse» и доставляется получателю — например замаскированная
    фишинговая ссылка `[жми](http://evil)` или поломанное форматирование.
    escape_markdown(version=1) экранирует спецсимволы legacy-Markdown (_ * [ `),
    нейтрализуя и ссылки (для ссылки обязателен `[`). Жирная обёртка `*...*`
    остаётся вокруг уже экранированного заголовка.
    """
    safe_title = escape_markdown(title or '', version=1)
    safe_message = escape_markdown(message or '', version=1)
    return f"*{safe_title}*\n\n{safe_message}"


class RateLimiter:
    """
    Rate limiter для Telegram API
    Telegram лимиты: 30 сообщений в секунду, 20 сообщений в минуту на пользователя
    """
    def __init__(self, max_per_second=25, max_per_minute_per_user=15):
        self.max_per_second = max_per_second
        self.max_per_minute_per_user = max_per_minute_per_user
        self.global_tokens = max_per_second
        self.user_tokens = {}  # {user_id: (tokens, last_reset)}
        self.last_global_reset = time.time()
        self.lock = asyncio.Lock()
    
    async def acquire(self, user_id: int) -> bool:
        """
        Получить разрешение на отправку сообщения
        Returns: True если можно отправлять, False если нужно подождать
        """
        async with self.lock:
            current_time = time.time()
            
            # Обновляем глобальные токены (каждую секунду)
            if current_time - self.last_global_reset >= 1.0:
                self.global_tokens = self.max_per_second
                self.last_global_reset = current_time
            
            # Проверяем глобальный лимит
            if self.global_tokens <= 0:
                return False
            
            # Обновляем токены для конкретного пользователя
            if user_id not in self.user_tokens:
                self.user_tokens[user_id] = (self.max_per_minute_per_user, current_time)
            
            tokens, last_reset = self.user_tokens[user_id]
            
            # Сбрасываем токены пользователя каждую минуту
            if current_time - last_reset >= 60.0:
                tokens = self.max_per_minute_per_user
                last_reset = current_time
            
            # Проверяем лимит пользователя
            if tokens <= 0:
                return False
            
            # Уменьшаем счётчики
            self.global_tokens -= 1
            self.user_tokens[user_id] = (tokens - 1, last_reset)
            
            return True
    
    async def wait_if_needed(self, user_id: int, max_wait_seconds=5):
        """Подождать если достигнут лимит"""
        for _ in range(max_wait_seconds * 10):  # Проверяем каждые 100мс
            if await self.acquire(user_id):
                return True
            await asyncio.sleep(0.1)
        return False


class TelegramNotificationService:
    """
    Профессиональный сервис уведомлений через Telegram
    """
    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.bot = None
        self.rate_limiter = RateLimiter()
        
        if self.bot_token:
            self.bot = Bot(token=self.bot_token)
        else:
            logger.warning("TELEGRAM_BOT_TOKEN не установлен!")
    
    def generate_idempotency_key(self, recipient_id: int, notification_type: str, data: Dict[str, Any]) -> str:
        """
        Генерировать ключ идемпотентности
        Предотвращает дублирование уведомлений
        """
        # Создаем уникальную строку из параметров
        unique_string = f"{recipient_id}:{notification_type}:{sorted(data.items())}"
        # Хешируем для компактности
        return hashlib.sha256(unique_string.encode()).hexdigest()
    
    def create_notification(
        self,
        recipient: User,
        notification_type: str,
        title: str,
        message: str,
        data: Optional[Dict[str, Any]] = None,
        scheduled_at: Optional[timezone.datetime] = None
    ) -> Optional[NotificationQueue]:
        """
        Создать уведомление в очереди
        С проверкой на дубликаты (идемпотентность)
        """
        try:
            data = data or {}
            
            # Генерируем ключ идемпотентности
            idempotency_key = self.generate_idempotency_key(
                recipient.id,
                notification_type,
                data
            )
            
            # Проверяем существует ли уже такое уведомление
            existing = NotificationQueue.objects.filter(
                idempotency_key=idempotency_key,
                status__in=['pending', 'processing', 'sent']
            ).first()
            
            if existing:
                logger.info(f"Уведомление с ключом {idempotency_key} уже существует")
                return existing
            
            # Проверяем настройки пользователя
            telegram_user = TelegramUser.objects.filter(
                user=recipient,
                notifications_enabled=True,
                started_bot=True
            ).first()
            
            if not telegram_user:
                logger.info(f"Пользователь {recipient.username} не подключил Telegram или отключил уведомления")
                return None
            
            # Создаем уведомление
            notification = NotificationQueue.objects.create(
                recipient=recipient,
                notification_type=notification_type,
                title=title,
                message=message,
                data=data,
                idempotency_key=idempotency_key,
                scheduled_at=scheduled_at or timezone.now()
            )
            
            logger.info(f"Создано уведомление {notification.id} для {recipient.get_full_name()}")
            return notification
            
        except Exception as e:
            logger.error(f"Ошибка создания уведомления: {e}")
            return None
    
    async def send_notification(self, notification: NotificationQueue) -> bool:
        """
        Отправить одно уведомление
        С обработкой ошибок и логированием
        """
        start_time = time.time()
        
        try:
            if not self.bot:
                raise Exception("Telegram bot не инициализирован")
            
            # Получаем Telegram профиль
            telegram_user = TelegramUser.objects.filter(
                user=notification.recipient,
                notifications_enabled=True,
                started_bot=True
            ).first()
            
            if not telegram_user:
                # Пользователь отключил уведомления
                notification.status = 'cancelled'
                notification.last_error = "Уведомления отключены"
                notification.save()
                
                NotificationLog.objects.create(
                    notification=notification,
                    attempt_number=notification.retry_count + 1,
                    status='skipped',
                    error_message='Уведомления отключены'
                )
                return False
            
            # Проверяем rate limiting
            if not await self.rate_limiter.wait_if_needed(telegram_user.telegram_id):
                # Превышен лимит, попробуем позже
                logger.warning(f"Rate limit достигнут для уведомления {notification.id}")
                notification.scheduled_at = timezone.now() + timedelta(seconds=60)
                notification.save()
                return False
            
            # Формируем сообщение (user-контент экранируется от Markdown-инъекции)
            full_message = build_notification_text(notification.title, notification.message)
            
            # Создаем кнопки если есть URL
            reply_markup = None
            if 'url' in notification.data:
                keyboard = [[InlineKeyboardButton(
                    notification.data.get('button_text', '📬 Открыть'),
                    url=notification.data['url']
                )]]
                reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Отправляем сообщение
            telegram_message = await self.bot.send_message(
                chat_id=telegram_user.telegram_id,
                text=full_message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
            # Успешно отправлено
            processing_time_ms = int((time.time() - start_time) * 1000)
            
            notification.mark_as_sent()
            
            NotificationLog.objects.create(
                notification=notification,
                attempt_number=notification.retry_count + 1,
                status='success',
                telegram_message_id=telegram_message.message_id,
                processing_time_ms=processing_time_ms
            )
            
            logger.info(f"Уведомление {notification.id} успешно отправлено за {processing_time_ms}мс")
            return True
            
        except RetryAfter as e:
            # Telegram просит подождать
            logger.warning(f"RetryAfter {e.retry_after}s для уведомления {notification.id}")
            notification.scheduled_at = timezone.now() + timedelta(seconds=e.retry_after + 5)
            notification.save()
            
            NotificationLog.objects.create(
                notification=notification,
                attempt_number=notification.retry_count + 1,
                status='error',
                error_message=f"Rate limit: подождать {e.retry_after}с"
            )
            return False
            
        except (TimedOut, NetworkError) as e:
            # Временные сетевые ошибки - можно повторить
            logger.warning(f"Временная ошибка для уведомления {notification.id}: {e}")
            
            notification.mark_as_failed(str(e))
            
            # Планируем повторную попытку с экспоненциальной задержкой
            if notification.can_retry():
                delay = notification.calculate_next_retry_delay()
                notification.scheduled_at = timezone.now() + delay
                notification.status = 'pending'
                notification.save()
                
                logger.info(f"Запланирована повторная попытка через {delay}")
            
            NotificationLog.objects.create(
                notification=notification,
                attempt_number=notification.retry_count,
                status='error',
                error_message=str(e),
                processing_time_ms=int((time.time() - start_time) * 1000)
            )
            return False
            
        except TelegramError as e:
            # Другие ошибки Telegram
            logger.error(f"Ошибка Telegram для уведомления {notification.id}: {e}")
            
            notification.mark_as_failed(str(e))
            
            # Не повторяем попытку для некоторых ошибок
            if 'blocked' in str(e).lower() or 'deleted' in str(e).lower():
                notification.status = 'cancelled'
                notification.last_error = "Пользователь заблокировал бота"
                notification.save()
            elif notification.can_retry():
                delay = notification.calculate_next_retry_delay()
                notification.scheduled_at = timezone.now() + delay
                notification.status = 'pending'
                notification.save()
            
            NotificationLog.objects.create(
                notification=notification,
                attempt_number=notification.retry_count,
                status='error',
                error_message=str(e),
                processing_time_ms=int((time.time() - start_time) * 1000)
            )
            return False
            
        except Exception as e:
            # Неожиданные ошибки
            logger.error(f"Неожиданная ошибка для уведомления {notification.id}: {e}", exc_info=True)
            
            notification.mark_as_failed(str(e))
            
            if notification.can_retry():
                delay = notification.calculate_next_retry_delay()
                notification.scheduled_at = timezone.now() + delay
                notification.status = 'pending'
                notification.save()
            
            NotificationLog.objects.create(
                notification=notification,
                attempt_number=notification.retry_count,
                status='error',
                error_message=str(e),
                processing_time_ms=int((time.time() - start_time) * 1000)
            )
            return False
    
    async def process_queue_batch(self, batch_size=10):
        """
        Обработать батч уведомлений из очереди
        """
        try:
            # Получаем уведомления готовые к отправке
            now = timezone.now()
            notifications = NotificationQueue.objects.filter(
                status='pending',
                scheduled_at__lte=now
            ).select_related('recipient').order_by('scheduled_at')[:batch_size]
            
            if not notifications:
                return 0
            
            logger.info(f"Обработка батча из {len(notifications)} уведомлений")
            
            # Отмечаем как обрабатываемые
            notification_ids = [n.id for n in notifications]
            NotificationQueue.objects.filter(id__in=notification_ids).update(
                status='processing',
                processing_started_at=now
            )
            
            # Обновляем объекты в памяти
            for notification in notifications:
                notification.status = 'processing'
            
            # Отправляем параллельно с контролем concurrency
            tasks = [self.send_notification(n) for n in notifications]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            success_count = sum(1 for r in results if r is True)
            logger.info(f"Успешно отправлено: {success_count}/{len(notifications)}")
            
            return success_count
            
        except Exception as e:
            logger.error(f"Ошибка обработки батча: {e}", exc_info=True)
            return 0
    
    @staticmethod
    def _tg_send_sync(token, chat_id, text, data=None):
        """Синхронная отправка сообщения в Telegram через HTTP API (urllib).

        Возвращает (ok, error_str, message_id). При ошибке парсинга Markdown
        повторяет отправку без parse_mode (защита от инъекции/битой разметки).
        """
        import json as _json
        from urllib import request as _req, parse as _parse, error as _err

        url = f'https://api.telegram.org/bot{token}/sendMessage'

        def _post(payload):
            body = _parse.urlencode(payload).encode('utf-8')
            req = _req.Request(url, data=body)
            try:
                with _req.urlopen(req, timeout=15) as resp:
                    return _json.loads(resp.read().decode('utf-8'))
            except _err.HTTPError as e:
                try:
                    return _json.loads(e.read().decode('utf-8'))
                except Exception:
                    return {'ok': False, 'description': f'HTTP {e.code}'}
            except Exception as e:
                return {'ok': False, 'description': str(e)}

        base = {'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
        if data and data.get('url'):
            base['reply_markup'] = _json.dumps({'inline_keyboard': [[{
                'text': data.get('button_text', '📬 Открыть'),
                'url': data['url'],
            }]]})

        result = _post(base)
        if not result.get('ok') and "can't parse" in (result.get('description', '').lower()):
            # Битая Markdown-разметка → шлём как обычный текст, чтобы доставить.
            base.pop('parse_mode', None)
            result = _post(base)

        if result.get('ok'):
            return True, '', result.get('result', {}).get('message_id', 0)
        return False, result.get('description', 'unknown error'), None

    def _deliver_one_sync(self, notification, token):
        """Синхронная доставка одного уведомления + запись лога/статуса."""
        start_time = time.time()
        try:
            telegram_user = TelegramUser.objects.filter(
                user=notification.recipient,
                notifications_enabled=True,
                started_bot=True,
            ).first()

            if not telegram_user:
                notification.status = 'cancelled'
                notification.last_error = 'Уведомления отключены'
                notification.save(update_fields=['status', 'last_error'])
                NotificationLog.objects.create(
                    notification=notification,
                    attempt_number=notification.retry_count + 1,
                    status='skipped', error_message='Уведомления отключены',
                )
                return False

            text = build_notification_text(notification.title, notification.message)
            ok, err, msg_id = self._tg_send_sync(
                token, telegram_user.telegram_id, text, notification.data,
            )
            elapsed = int((time.time() - start_time) * 1000)

            if ok:
                notification.mark_as_sent()
                NotificationLog.objects.create(
                    notification=notification,
                    attempt_number=notification.retry_count + 1,
                    status='success', telegram_message_id=msg_id or 0,
                    processing_time_ms=elapsed,
                )
                return True

            # Ошибка доставки
            notification.mark_as_failed(err)
            low = err.lower()
            if 'blocked' in low or 'deactivated' in low or 'chat not found' in low:
                notification.status = 'cancelled'
                notification.last_error = 'Пользователь недоступен'
                notification.save(update_fields=['status', 'last_error'])
            elif notification.can_retry():
                notification.scheduled_at = timezone.now() + notification.calculate_next_retry_delay()
                notification.status = 'pending'
                notification.save(update_fields=['scheduled_at', 'status'])
            NotificationLog.objects.create(
                notification=notification,
                attempt_number=notification.retry_count,
                status='error', error_message=err, processing_time_ms=elapsed,
            )
            return False
        except Exception as e:
            logger.error(f"Ошибка доставки уведомления {notification.id}: {e}", exc_info=True)
            try:
                notification.mark_as_failed(str(e))
                if notification.can_retry():
                    notification.scheduled_at = timezone.now() + notification.calculate_next_retry_delay()
                    notification.status = 'pending'
                    notification.save(update_fields=['scheduled_at', 'status'])
            except Exception:
                pass
            return False

    def process_queue_batch_sync(self, batch_size=10):
        """Синхронная обработка батча очереди.

        Race-safe: батч забирается под select_for_update(skip_locked=True) и
        атомарно помечается 'processing' — два воркера не возьмут одно и то же
        (раньше выборка и пометка были не атомарны → дубли сообщений).
        Доставка полностью синхронная (без async-ORM, который ломается под ASGI).
        """
        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or ''
        if not token:
            logger.error('TELEGRAM_BOT_TOKEN не задан — очередь не обрабатывается')
            return 0
        try:
            now = timezone.now()
            with transaction.atomic():
                rows = list(
                    NotificationQueue.objects
                    .select_for_update(skip_locked=True)
                    .filter(status='pending', scheduled_at__lte=now)
                    .select_related('recipient')
                    .order_by('scheduled_at')[:batch_size]
                )
                ids = [n.id for n in rows]
                if ids:
                    NotificationQueue.objects.filter(id__in=ids).update(
                        status='processing', processing_started_at=now,
                    )
            if not rows:
                return 0

            success_count = 0
            for notification in rows:
                if self._deliver_one_sync(notification, token):
                    success_count += 1
            logger.info(f"Очередь: отправлено {success_count}/{len(rows)}")
            return success_count
        except Exception as e:
            logger.error(f"Ошибка в process_queue_batch_sync: {e}", exc_info=True)
            return 0
    
    def notify_new_message(
        self,
        recipient: User,
        sender_name: str,
        message_preview: str,
        conversation_id: str
    ) -> Optional[NotificationQueue]:
        """
        Создать уведомление о новом сообщении
        """
        title = "💬 Новое сообщение!"
        message = f"От: *{sender_name}*\n\n_{message_preview[:150]}{'...' if len(message_preview) > 150 else ''}_"
        
        # URL для перехода в диалог
        conversation_url = f"{settings.SITE_URL}/messages/{conversation_id}/"
        
        data = {
            'sender_name': sender_name,
            'conversation_id': str(conversation_id),
            'url': conversation_url,
            'button_text': '📬 Открыть диалог'
        }
        
        return self.create_notification(
            recipient=recipient,
            notification_type='new_message',
            title=title,
            message=message,
            data=data
        )


# Глобальный экземпляр сервиса
notification_service = TelegramNotificationService()


# Удобные функции для использования
def queue_new_message_notification(recipient: User, sender_name: str, message_preview: str, conversation_id: str):
    """
    Добавить уведомление о новом сообщении в очередь
    
    Использование:
        from telegram_bot.notification_service import queue_new_message_notification
        queue_new_message_notification(user, "Иван Иванов", "Привет!", conversation.id)
    """
    return notification_service.notify_new_message(
        recipient, sender_name, message_preview, conversation_id
    )


def process_notification_queue(batch_size=10):
    """
    Обработать очередь уведомлений (синхронно)
    
    Использование:
        from telegram_bot.notification_service import process_notification_queue
        sent_count = process_notification_queue()
    """
    return notification_service.process_queue_batch_sync(batch_size)
