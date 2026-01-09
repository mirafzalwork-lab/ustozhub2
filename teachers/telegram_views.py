"""
API Views для интеграции с Telegram WebApp
"""

import hashlib
import hmac
import json
from urllib.parse import parse_qs
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth import login
from django.conf import settings
from .models import TelegramUser, User
import logging

logger = logging.getLogger(__name__)


def verify_telegram_auth(auth_data: dict, bot_token: str) -> bool:
    """
    Проверяет подлинность данных от Telegram WebApp
    
    Args:
        auth_data: Данные авторизации от Telegram
        bot_token: Токен бота
        
    Returns:
        bool: True если данные валидны
    """
    try:
        # ✅ Создаем копию чтобы не мутировать оригинал
        auth_data_copy = dict(auth_data)
        
        # Получаем hash из данных
        received_hash = auth_data_copy.pop('hash', None)
        if not received_hash:
            logger.warning("Telegram auth: отсутствует hash")
            return False
        
        # Сортируем данные и создаем строку для проверки
        data_check_string = '\n'.join([f'{k}={v}' for k, v in sorted(auth_data_copy.items())])
        
        # Создаем секретный ключ
        secret_key = hashlib.sha256(bot_token.encode()).digest()
        
        # Вычисляем hash
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Сравниваем хеши
        is_valid = calculated_hash == received_hash
        
        if not is_valid:
            logger.warning(f"Telegram auth: неверная подпись данных")
        
        return is_valid
        
    except Exception as e:
        logger.error(f"Ошибка проверки Telegram auth: {e}", exc_info=True)
        return False


@csrf_exempt
@require_http_methods(["POST"])
def telegram_auth(request):
    """
    API endpoint для аутентификации через Telegram WebApp
    
    Принимает данные от Telegram WebApp, проверяет их подлинность,
    создает/находит пользователя и авторизует его в Django
    
    POST параметры:
        - id: Telegram user ID
        - first_name: Имя
        - last_name: Фамилия (опционально)
        - username: Username в Telegram (опционально)
        - auth_date: Дата авторизации
        - hash: Подпись для проверки
    """
    try:
        # Парсим данные из запроса
        try:
            if request.content_type == 'application/json':
                data = json.loads(request.body)
            else:
                data = dict(parse_qs(request.body.decode()))
                # Убираем списки из значений
                data = {k: v[0] if isinstance(v, list) else v for k, v in data.items()}
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Telegram auth: ошибка парсинга данных - {e}")
            return JsonResponse({
                'success': False,
                'error': 'Неверный формат данных'
            }, status=400)
        
        # Проверяем наличие обязательных полей
        if 'id' not in data or 'hash' not in data:
            logger.warning(f"Telegram auth: недостаточно данных - {list(data.keys())}")
            return JsonResponse({
                'success': False,
                'error': 'Недостаточно данных для авторизации'
            }, status=400)
        
        # ✅ Безопасное преобразование telegram_id
        try:
            telegram_id = int(data['id'])
            if telegram_id <= 0:
                raise ValueError("Telegram ID должен быть положительным")
        except (ValueError, TypeError) as e:
            logger.warning(f"Telegram auth: неверный ID - {data.get('id')}")
            return JsonResponse({
                'success': False,
                'error': 'Неверный формат Telegram ID'
            }, status=400)
        
        # Проверяем подлинность данных (в продакшене обязательно!)
        bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', None)
        if not bot_token:
            logger.error("Telegram auth: отсутствует TELEGRAM_BOT_TOKEN в settings")
            return JsonResponse({
                'success': False,
                'error': 'Сервис временно недоступен'
            }, status=503)
        
        if not verify_telegram_auth(data, bot_token):
            logger.warning(f"Telegram auth: неверная подпись для ID {telegram_id}")
            return JsonResponse({
                'success': False,
                'error': 'Неверная подпись данных'
            }, status=403)
        
        # ✅ Валидация и очистка данных
        first_name = str(data.get('first_name', '')).strip()[:150]
        last_name = str(data.get('last_name', '')).strip()[:150]
        username = str(data.get('username', '')).strip()[:100]
        
        if not first_name:
            first_name = "Telegram User"
        
        # Ищем или создаем TelegramUser
        telegram_user, created = TelegramUser.objects.get_or_create(
            telegram_id=telegram_id,
            defaults={
                'telegram_username': username,
                'first_name': first_name,
                'last_name': last_name,
                'started_bot': True,
            }
        )
        
        # Обновляем данные если пользователь уже существует
        if not created:
            telegram_user.telegram_username = username
            telegram_user.first_name = first_name
            telegram_user.last_name = last_name
            telegram_user.started_bot = True
            telegram_user.save()
        
        # Проверяем, привязан ли Telegram аккаунт к пользователю платформы
        if telegram_user.user:
            # Пользователь уже зарегистрирован - авторизуем его
            login(request, telegram_user.user, backend='django.contrib.auth.backends.ModelBackend')
            
            logger.info(
                f"✅ Telegram auth успешно: user_id={telegram_user.user.pk}, "
                f"telegram_id={telegram_id}"
            )
            
            return JsonResponse({
                'success': True,
                'user_exists': True,
                'user': {
                    'id': telegram_user.user.id,
                    'username': telegram_user.user.username,
                    'email': telegram_user.user.email,
                    'full_name': telegram_user.user.get_full_name(),
                    'user_type': telegram_user.user.user_type,
                    'avatar': telegram_user.user.avatar.url if telegram_user.user.avatar else None,
                },
                'redirect_url': '/profile/'
            })
        else:
            # Пользователь новый - нужна регистрация
            logger.info(f"Telegram auth: новый пользователь telegram_id={telegram_id}")
            
            return JsonResponse({
                'success': True,
                'user_exists': False,
                'telegram_data': {
                    'telegram_id': telegram_id,
                    'first_name': first_name,
                    'last_name': last_name,
                    'username': username,
                },
                'redirect_url': '/register/choose/'
            })
            
    except Exception as e:
        logger.error(f"Ошибка в telegram_auth: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'Внутренняя ошибка сервера'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def link_telegram_account(request):
    """
    Связывает существующий аккаунт пользователя с Telegram
    
    POST параметры:
        - telegram_id: ID пользователя в Telegram
        - user_id: ID пользователя в Django (или использовать текущего авторизованного)
    """
    try:
        if not request.user.is_authenticated:
            return JsonResponse({
                'success': False,
                'error': 'Требуется авторизация'
            }, status=401)
        
        # ✅ Безопасный парсинг JSON
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError as e:
            logger.warning(f"Link Telegram: ошибка парсинга JSON - {e}")
            return JsonResponse({
                'success': False,
                'error': 'Неверный формат данных'
            }, status=400)
        
        # ✅ Безопасное преобразование telegram_id
        telegram_id_raw = data.get('telegram_id')
        if not telegram_id_raw:
            return JsonResponse({
                'success': False,
                'error': 'Не указан telegram_id'
            }, status=400)
        
        try:
            telegram_id = int(telegram_id_raw)
            if telegram_id <= 0:
                raise ValueError("ID должен быть положительным")
        except (ValueError, TypeError) as e:
            logger.warning(f"Link Telegram: неверный telegram_id - {telegram_id_raw}")
            return JsonResponse({
                'success': False,
                'error': 'Неверный формат telegram_id'
            }, status=400)
        
        # Проверяем, существует ли TelegramUser
        try:
            telegram_user = TelegramUser.objects.get(telegram_id=telegram_id)
            
            # Проверяем, не привязан ли уже к другому пользователю
            if telegram_user.user and telegram_user.user.pk != request.user.pk:
                logger.warning(
                    f"Link Telegram: попытка привязать уже используемый аккаунт "
                    f"telegram_id={telegram_id} к user_id={request.user.pk}"
                )
                return JsonResponse({
                    'success': False,
                    'error': 'Этот Telegram аккаунт уже привязан к другому пользователю'
                }, status=400)
            
            # Привязываем к текущему пользователю
            telegram_user.user = request.user
            telegram_user.save()
            
            logger.info(
                f"✅ Telegram аккаунт привязан: user_id={request.user.pk}, "
                f"telegram_id={telegram_id}"
            )
            
            return JsonResponse({
                'success': True,
                'message': 'Telegram аккаунт успешно привязан'
            })
            
        except TelegramUser.DoesNotExist:
            logger.warning(
                f"Link Telegram: TelegramUser не найден telegram_id={telegram_id}"
            )
            return JsonResponse({
                'success': False,
                'error': 'Telegram пользователь не найден. Сначала нажмите /start в боте.'
            }, status=404)
            
    except Exception as e:
        logger.error(f"Ошибка в link_telegram_account: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'Внутренняя ошибка сервера'
        }, status=500)


@require_http_methods(["GET"])
def telegram_status(request):
    """
    Проверяет статус подключения Telegram для текущего пользователя
    """
    if not request.user.is_authenticated:
        return JsonResponse({
            'authenticated': False,
            'telegram_connected': False
        })
    
    try:
        telegram_user = TelegramUser.objects.get(user=request.user)
        return JsonResponse({
            'authenticated': True,
            'telegram_connected': True,
            'telegram_username': telegram_user.telegram_username or '',
            'notifications_enabled': telegram_user.notifications_enabled,
        })
    except TelegramUser.DoesNotExist:
        return JsonResponse({
            'authenticated': True,
            'telegram_connected': False
        })
    except Exception as e:
        logger.error(f"Ошибка в telegram_status для user_id={request.user.pk}: {e}", exc_info=True)
        return JsonResponse({
            'authenticated': True,
            'telegram_connected': False,
            'error': 'Ошибка проверки статуса'
        }, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def toggle_notifications(request):
    """
    Включить/выключить уведомления в Telegram
    """
    if not request.user.is_authenticated:
        return JsonResponse({
            'success': False,
            'error': 'Требуется авторизация'
        }, status=401)
    
    try:
        telegram_user = TelegramUser.objects.get(user=request.user)
        telegram_user.notifications_enabled = not telegram_user.notifications_enabled
        telegram_user.save()
        
        logger.info(
            f"Уведомления Telegram {'включены' if telegram_user.notifications_enabled else 'выключены'} "
            f"для user_id={request.user.pk}"
        )
        
        return JsonResponse({
            'success': True,
            'notifications_enabled': telegram_user.notifications_enabled
        })
        
    except TelegramUser.DoesNotExist:
        logger.warning(f"Toggle notifications: TelegramUser не найден для user_id={request.user.pk}")
        return JsonResponse({
            'success': False,
            'error': 'Telegram аккаунт не подключен'
        }, status=404)
    except Exception as e:
        logger.error(f"Ошибка в toggle_notifications для user_id={request.user.pk}: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': 'Внутренняя ошибка сервера'
        }, status=500)


