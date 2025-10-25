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
        # Получаем hash из данных
        received_hash = auth_data.pop('hash', None)
        if not received_hash:
            return False
        
        # Сортируем данные и создаем строку для проверки
        data_check_string = '\n'.join([f'{k}={v}' for k, v in sorted(auth_data.items())])
        
        # Создаем секретный ключ
        secret_key = hashlib.sha256(bot_token.encode()).digest()
        
        # Вычисляем hash
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Сравниваем хеши
        return calculated_hash == received_hash
        
    except Exception as e:
        logger.error(f"Ошибка проверки Telegram auth: {e}")
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
        if request.content_type == 'application/json':
            data = json.loads(request.body)
        else:
            data = dict(parse_qs(request.body.decode()))
            # Убираем списки из значений
            data = {k: v[0] if isinstance(v, list) else v for k, v in data.items()}
        
        # Проверяем наличие обязательных полей
        if 'id' not in data or 'hash' not in data:
            return JsonResponse({
                'success': False,
                'error': 'Недостаточно данных для авторизации'
            }, status=400)
        
        # Проверяем подлинность данных (в продакшене обязательно!)
        bot_token = settings.TELEGRAM_BOT_TOKEN
        if not verify_telegram_auth(data.copy(), bot_token):
            return JsonResponse({
                'success': False,
                'error': 'Неверная подпись данных'
            }, status=403)
        
        telegram_id = int(data['id'])
        first_name = data.get('first_name', '')
        last_name = data.get('last_name', '')
        username = data.get('username', '')
        
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
            telegram_user.save()
        
        # Проверяем, привязан ли Telegram аккаунт к пользователю платформы
        if telegram_user.user:
            # Пользователь уже зарегистрирован - авторизуем его
            login(request, telegram_user.user, backend='django.contrib.auth.backends.ModelBackend')
            
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
        logger.error(f"Ошибка в telegram_auth: {e}")
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
        
        data = json.loads(request.body)
        telegram_id = int(data.get('telegram_id'))
        
        if not telegram_id:
            return JsonResponse({
                'success': False,
                'error': 'Не указан telegram_id'
            }, status=400)
        
        # Проверяем, существует ли TelegramUser
        try:
            telegram_user = TelegramUser.objects.get(telegram_id=telegram_id)
            
            # Проверяем, не привязан ли уже к другому пользователю
            if telegram_user.user and telegram_user.user != request.user:
                return JsonResponse({
                    'success': False,
                    'error': 'Этот Telegram аккаунт уже привязан к другому пользователю'
                }, status=400)
            
            # Привязываем к текущему пользователю
            telegram_user.user = request.user
            telegram_user.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Telegram аккаунт успешно привязан'
            })
            
        except TelegramUser.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Telegram пользователь не найден. Сначала нажмите /start в боте.'
            }, status=404)
            
    except Exception as e:
        logger.error(f"Ошибка в link_telegram_account: {e}")
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
            'telegram_username': telegram_user.telegram_username,
            'notifications_enabled': telegram_user.notifications_enabled,
        })
    except TelegramUser.DoesNotExist:
        return JsonResponse({
            'authenticated': True,
            'telegram_connected': False
        })


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
        
        return JsonResponse({
            'success': True,
            'notifications_enabled': telegram_user.notifications_enabled
        })
        
    except TelegramUser.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Telegram аккаунт не подключен'
        }, status=404)


