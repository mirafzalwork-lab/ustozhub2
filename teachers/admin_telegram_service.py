"""
Сервис для отправки сообщений через админ панель
Отдельный от системы уведомлений для избежания конфликтов
"""

import logging
import asyncio
import time
import json
import urllib.request
import urllib.parse
import urllib.error
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
        
        print(f"🔑 TELEGRAM DEBUG: Инициализация сервиса с токеном: {self.bot_token[:20] if self.bot_token else 'НЕТ ТОКЕНА'}...")
        
        if self.bot_token:
            try:
                self.bot = Bot(token=self.bot_token)
                print("✅ TELEGRAM DEBUG: Bot объект создан успешно")
            except Exception as e:
                print(f"❌ TELEGRAM DEBUG: Ошибка создания Bot: {e}")
                logger.error(f"Ошибка создания Telegram Bot: {e}")
        else:
            print("❌ TELEGRAM DEBUG: TELEGRAM_BOT_TOKEN не установлен!")
            logger.error("TELEGRAM_BOT_TOKEN не установлен!")
    
    def send_message_sync(self, telegram_id: int, text: str, reply_markup=None, parse_mode=None) -> bool:
        """Синхронная отправка сообщения"""
        if not self.bot:
            logger.error("Telegram bot не инициализирован")
            return False
        
        try:
            # Проверяем наличие активного event loop
            try:
                loop = asyncio.get_running_loop()
                # Если loop уже запущен, создаем новый в отдельном потоке
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(self._run_in_new_loop, telegram_id, text, reply_markup, parse_mode)
                    return future.result(timeout=30)
            except RuntimeError:
                # Нет активного loop, создаем новый
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    return loop.run_until_complete(
                        self._send_message_async(telegram_id, text, reply_markup, parse_mode)
                    )
                finally:
                    loop.close()
        except Exception as e:
            logger.error(f"Ошибка в send_message_sync: {e}")
            return False
    
    def _run_in_new_loop(self, telegram_id: int, text: str, reply_markup=None, parse_mode=None) -> bool:
        """Запуск в новом event loop"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                self._send_message_async(telegram_id, text, reply_markup, parse_mode)
            )
        finally:
            loop.close()
    
    async def _send_message_async(self, telegram_id: int, text: str, reply_markup=None, parse_mode=None) -> bool:
        """Асинхронная отправка сообщения"""
        try:
            await self.bot.send_message(
                chat_id=telegram_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            logger.info(f"✅ Сообщение отправлено пользователю {telegram_id}")
            return True
            
        except TelegramError as e:
            error_msg = str(e).lower()
            # Специальная обработка для заблокированных пользователей
            if any(phrase in error_msg for phrase in ['chat not found', 'bot was blocked', 'user is deactivated', 'forbidden']):
                logger.warning(f"🚫 Пользователь {telegram_id} заблокировал бота или удалил чат")
                # Отмечаем пользователя как неактивного
                try:
                    from .models import TelegramUser
                    TelegramUser.objects.filter(telegram_id=telegram_id).update(
                        started_bot=False, 
                        notifications_enabled=False,
                        last_interaction=timezone.now()
                    )
                except Exception as update_error:
                    logger.error(f"Ошибка обновления статуса пользователя {telegram_id}: {update_error}")
            else:
                logger.error(f"❌ Ошибка отправки сообщения пользователю {telegram_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка отправки сообщения пользователю {telegram_id}: {e}")
            return False
    
    def send_message_simple(self, telegram_id: int, text: str, reply_markup=None, parse_mode=None) -> bool:
        """Упрощенная синхронная отправка без async"""
        if not self.bot:
            logger.error("Telegram bot не инициализирован")
            return False
        
        try:
            # Используем прямой API запрос через urllib
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            
            # Проверяем и ограничиваем длину сообщения (Telegram лимит 4096 символов)
            if len(text) > 4096:
                text = text[:4093] + "..."
                logger.warning(f"⚠️ Сообщение для пользователя {telegram_id} обрезано до 4096 символов")
            
            # Убираем потенциально проблемные символы для Markdown
            if parse_mode == 'Markdown':
                # Экранируем специальные символы Markdown если они не используются правильно
                text = text.replace('_', r'\_').replace('*', r'\*').replace('[', r'\[').replace('`', r'\`')
            
            payload = {
                'chat_id': str(telegram_id),  # Убеждаемся что это строка
                'text': text,
                'parse_mode': parse_mode or 'Markdown'
            }
            
            logger.debug(f"🔍 Отправляем сообщение пользователю {telegram_id}, длина текста: {len(text)} символов")
            
            # Кодируем данные как form-data (более надежно для Telegram API)
            data = urllib.parse.urlencode(payload).encode('utf-8')
            
            # Создаем запрос с заголовками
            req = urllib.request.Request(
                url, 
                data=data,
                headers={
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'User-Agent': 'Django-Telegram-Bot/1.0'
                }
            )
            
            # Отправляем с таймаутом
            with urllib.request.urlopen(req, timeout=30) as response:
                response_data = response.read().decode('utf-8')
                result = json.loads(response_data)
                
                if result.get('ok'):
                    logger.info(f"✅ Сообщение отправлено пользователю {telegram_id}")
                    return True
                else:
                    error_description = result.get('description', 'Неизвестная ошибка')
                    
                    # Обработка заблокированных пользователей
                    if any(phrase in error_description.lower() for phrase in ['chat not found', 'bot was blocked', 'user is deactivated', 'forbidden']):
                        logger.warning(f"🚫 Пользователь {telegram_id} заблокировал бота: {error_description}")
                        # Отмечаем пользователя как неактивного
                        try:
                            TelegramUser.objects.filter(telegram_id=telegram_id).update(
                                started_bot=False, 
                                notifications_enabled=False,
                                last_interaction=timezone.now()
                            )
                        except Exception:
                            pass
                    else:
                        logger.error(f"❌ API ошибка для пользователя {telegram_id}: {error_description}")
                    return False
                
        except urllib.error.HTTPError as e:
            # Читаем тело ответа для получения подробной ошибки
            try:
                error_body = e.read().decode('utf-8')
                error_details = json.loads(error_body)
                error_description = error_details.get('description', 'Неизвестная ошибка')
                
                # Проверяем, заблокирован ли бот пользователем
                if any(phrase in error_description.lower() for phrase in ['bot was blocked', 'user is deactivated', 'forbidden']):
                    logger.warning(f"🚫 Пользователь {telegram_id} заблокировал бота: {error_description}")
                    # Отмечаем пользователя как неактивного
                    try:
                        TelegramUser.objects.filter(telegram_id=telegram_id).update(
                            started_bot=False, 
                            notifications_enabled=False,
                            last_interaction=timezone.now()
                        )
                        logger.info(f"🔄 Пользователь {telegram_id} отключен от рассылок")
                    except Exception as update_error:
                        logger.error(f"❌ Ошибка обновления статуса пользователя {telegram_id}: {update_error}")
                else:
                    logger.error(f"❌ HTTP ошибка {e.code} для пользователя {telegram_id}: {error_description}")
            except:
                logger.error(f"❌ HTTP ошибка {e.code} для пользователя {telegram_id}: {e.reason}")
            return False
        except urllib.error.URLError as e:
            if 'timeout' in str(e).lower():
                logger.error(f"⏰ Таймаут при отправке пользователю {telegram_id}")
            else:
                logger.error(f"🌐 Ошибка сети при отправке пользователю {telegram_id}: {e}")
            return False
        except json.JSONDecodeError as e:
            logger.error(f"❌ Ошибка декодирования JSON для пользователя {telegram_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка отправки пользователю {telegram_id}: {e}")
            return False
    
    def send_to_selected_users(self, telegram_users: List[TelegramUser], message: str, parse_mode='Markdown') -> Dict[str, int]:
        """
        Отправить сообщение выбранным пользователям батчами
        
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
        
        # Размер батча - отправляем по 50 пользователей за раз
        BATCH_SIZE = 50
        DELAY_BETWEEN_BATCHES = 2  # секунды пауза между батчами
        DELAY_BETWEEN_MESSAGES = 0.1  # секунды пауза между сообщениями
        
        logger.info(f"📤 Начинаем отправку {stats['total']} пользователям батчами по {BATCH_SIZE}")
        
        # Разбиваем на батчи
        for batch_num in range(0, len(telegram_users), BATCH_SIZE):
            batch = telegram_users[batch_num:batch_num + BATCH_SIZE]
            batch_start = batch_num + 1
            batch_end = min(batch_num + BATCH_SIZE, len(telegram_users))
            
            print(f"📦 Обрабатываем батч {batch_start}-{batch_end} из {len(telegram_users)}")
            logger.info(f"📦 Обрабатываем батч {batch_start}-{batch_end} из {len(telegram_users)}")
            
            batch_success = 0
            batch_failed = 0
            
            for i, tg_user in enumerate(batch):
                user_num = batch_num + i + 1
                
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
                try:
                    success = self.send_message_simple(
                        telegram_id=tg_user.telegram_id,
                        text=message,
                        parse_mode=parse_mode
                    )

                    if success:
                        stats['success'] += 1
                        batch_success += 1
                        stats['details'].append({
                            'user': f"{tg_user.first_name} (@{tg_user.telegram_username or 'нет'})",
                            'status': 'success',
                            'reason': 'Отправлено успешно'
                        })
                    else:
                        batch_failed += 1
                        # Проверяем, был ли пользователь автоматически деактивирован
                        updated_user = TelegramUser.objects.filter(telegram_id=tg_user.telegram_id).first()
                        if updated_user and not updated_user.started_bot:
                            stats['details'].append({
                                'user': f"{tg_user.first_name} (@{tg_user.telegram_username or 'нет'})",
                                'status': 'blocked',
                                'reason': 'Заблокировал бота (автоматически отключен)'
                            })
                        else:
                            stats['failed'] += 1
                            stats['details'].append({
                                'user': f"{tg_user.first_name} (@{tg_user.telegram_username or 'нет'})",
                                'status': 'failed',
                                'reason': 'Ошибка отправки'
                            })
                        logger.warning(f"❌ Не удалось отправить пользователю {tg_user.telegram_id}")
                except Exception as e:
                    logger.error(f"❌ Исключение при отправке пользователю {tg_user.telegram_id}: {e}")
                    stats['failed'] += 1
                    batch_failed += 1
                    stats['details'].append({
                        'user': f"{tg_user.first_name} (@{tg_user.telegram_username or 'нет'})",
                        'status': 'failed',
                        'reason': f'Исключение: {str(e)}'
                    })
                
                # Пауза между сообщениями
                time.sleep(DELAY_BETWEEN_MESSAGES)
            
            # Логирование результатов батча
            print(f"📊 Батч {batch_start}-{batch_end} завершен: ✅ {batch_success} успешно, ❌ {batch_failed} ошибок")
            logger.info(f"📊 Батч {batch_start}-{batch_end} завершен: ✅ {batch_success} успешно, ❌ {batch_failed} ошибок")
            
            # Пауза между батчами
            if batch_end < len(telegram_users):
                print(f"⏳ Пауза {DELAY_BETWEEN_BATCHES} сек перед следующим батчем...")
                logger.info(f"⏳ Пауза {DELAY_BETWEEN_BATCHES} сек перед следующим батчем...")
                time.sleep(DELAY_BETWEEN_BATCHES)
        
        print(f"🏁 МАССОВАЯ РАССЫЛКА ЗАВЕРШЕНА!")
        print(f"📊 Итоговые результаты: ✅ {stats['success']} успешно, ❌ {stats['failed']} ошибок, 📊 {stats['total']} всего")
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
        if not self.bot:
            logger.error("❌ Telegram bot не инициализирован!")
            return {'success': 0, 'failed': 0, 'total': 0, 'details': []}
        
        logger.info(f"🚀 Начинаем подготовку массовой рассылки. Текст: {message[:50]}...")
        
        # Получаем всех пользователей, которые запустили бота
        queryset = TelegramUser.objects.filter(
            started_bot=True,
            notifications_enabled=True
        )
        
        total_count = queryset.count()
        logger.info(f"📊 Найдено {total_count} пользователей с включенными уведомлениями")
        
        # Применяем фильтр по типу пользователя если указан
        if user_type:
            queryset = queryset.filter(user__user_type=user_type)
            filtered_count = queryset.count()
            logger.info(f"📊 После фильтрации по типу '{user_type}': {filtered_count} пользователей")
        
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

