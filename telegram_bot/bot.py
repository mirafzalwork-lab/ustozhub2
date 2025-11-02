"""
Telegram бот для TeacherHub
Обрабатывает команды /start и отображает WebApp
"""

import os
import sys
import logging
import asyncio
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    ContextTypes,
    MessageHandler,
    filters
)

# Добавляем путь к Django проекту
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')

import django
django.setup()

from teachers.models import TelegramUser
from django.conf import settings
from asgiref.sync import sync_to_async

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик команды /start
    Создает/обновляет пользователя в БД и показывает кнопку WebApp
    """
    user = update.effective_user
    
    try:
        # Создаем или обновляем пользователя в БД (асинхронно)
        telegram_user, created = await sync_to_async(TelegramUser.objects.update_or_create)(
            telegram_id=user.id,
            defaults={
                'telegram_username': user.username or '',
                'first_name': user.first_name or '',
                'last_name': user.last_name or '',
                'language_code': user.language_code or 'ru',
                'started_bot': True,
            }
        )
        
        if created:
            logger.info(f"Новый пользователь зарегистрирован: {user.id} (@{user.username})")
        else:
            logger.info(f"Пользователь вернулся: {user.id} (@{user.username})")
        
        # Создаем кнопку с WebApp
        webapp_url = settings.TELEGRAM_WEBAPP_URL
        keyboard = [
            [InlineKeyboardButton(
                "🌐 Открыть TeacherHub",
                web_app=WebAppInfo(url=webapp_url)
            )],
            [InlineKeyboardButton(
                "📚 О платформе",
                callback_data="about"
            )],
            [InlineKeyboardButton(
                "⚙️ Настройки уведомлений",
                callback_data="settings"
            )]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Приветственное сообщение
        welcome_text = (
            f"👋 Привет, {user.first_name}!\n\n"
            f"Добро пожаловать в **TeacherHub** — платформу для поиска учителей и учеников!\n\n"
            f"🎓 **Что вы можете сделать:**\n"
            f"• Найти идеального учителя по любому предмету\n"
            f"• Разместить свой профиль как учитель\n"
            f"• Получать уведомления о новых сообщениях\n"
            f"• Следить за обновлениями платформы\n\n"
            f"👇 Нажмите кнопку ниже, чтобы открыть платформу:"
        )
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Ошибка в start_command: {e}")
        await update.message.reply_text(
            "Произошла ошибка. Пожалуйста, попробуйте позже."
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = (
        "📖 **Справка по боту TeacherHub**\n\n"
        "**Доступные команды:**\n"
        "/start - Начать работу с ботом\n"
        "/help - Показать эту справку\n"
        "/profile - Открыть ваш профиль\n"
        "/notifications - Управление уведомлениями\n\n"
        "Если у вас есть вопросы, свяжитесь с нами: @teacherhub_support"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /profile"""
    user = update.effective_user
    
    try:
        telegram_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
        
        if telegram_user.user:
            # Пользователь привязан к аккаунту
            webapp_url = f"{settings.TELEGRAM_WEBAPP_URL}/profile"
            keyboard = [[InlineKeyboardButton(
                "👤 Открыть профиль",
                web_app=WebAppInfo(url=webapp_url)
            )]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            profile_text = (
                f"👤 **Ваш профиль**\n\n"
                f"Имя: {telegram_user.user.get_full_name()}\n"
                f"Тип: {telegram_user.user.get_user_type_display()}\n"
                f"Email: {telegram_user.user.email}\n\n"
                f"Нажмите кнопку ниже, чтобы редактировать профиль."
            )
            
            await update.message.reply_text(
                profile_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        else:
            # Пользователь еще не зарегистрирован
            webapp_url = settings.TELEGRAM_WEBAPP_URL
            keyboard = [[InlineKeyboardButton(
                "📝 Зарегистрироваться",
                web_app=WebAppInfo(url=webapp_url)
            )]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                "Вы еще не зарегистрированы на платформе.\n"
                "Нажмите кнопку ниже, чтобы создать аккаунт:",
                reply_markup=reply_markup
            )
            
    except TelegramUser.DoesNotExist:
        await update.message.reply_text(
            "Пожалуйста, сначала используйте команду /start"
        )
    except Exception as e:
        logger.error(f"Ошибка в profile_command: {e}")
        await update.message.reply_text(
            "Произошла ошибка. Пожалуйста, попробуйте позже."
        )


async def notifications_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /notifications - управление уведомлениями"""
    user = update.effective_user
    
    try:
        telegram_user = await sync_to_async(TelegramUser.objects.get)(telegram_id=user.id)
        
        # Переключаем статус уведомлений
        telegram_user.notifications_enabled = not telegram_user.notifications_enabled
        await sync_to_async(telegram_user.save)()
        
        status = "включены ✅" if telegram_user.notifications_enabled else "выключены ❌"
        
        keyboard = [[InlineKeyboardButton(
            f"{'🔕 Выключить' if telegram_user.notifications_enabled else '🔔 Включить'} уведомления",
            callback_data="toggle_notifications"
        )]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"Уведомления {status}\n\n"
            f"Вы {'будете' if telegram_user.notifications_enabled else 'не будете'} получать:"
            f"\n• Уведомления о новых сообщениях"
            f"\n• Важные обновления платформы"
            f"\n• Новости и акции",
            reply_markup=reply_markup
        )
        
    except TelegramUser.DoesNotExist:
        await update.message.reply_text(
            "Пожалуйста, сначала используйте команду /start"
        )
    except Exception as e:
        logger.error(f"Ошибка в notifications_command: {e}")
        await update.message.reply_text(
            "Произошла ошибка. Пожалуйста, попробуйте позже."
        )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback-кнопок"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "about":
        about_text = (
            "📚 **О платформе TeacherHub**\n\n"
            "TeacherHub — это современная платформа для поиска учителей и учеников.\n\n"
            "**Для учителей:**\n"
            "• Создайте профиль и расскажите о своих навыках\n"
            "• Укажите предметы и цены\n"
            "• Получайте запросы от учеников\n\n"
            "**Для учеников:**\n"
            "• Найдите учителя по любому предмету\n"
            "• Сравните цены и отзывы\n"
            "• Свяжитесь напрямую через платформу"
        )
        await query.edit_message_text(about_text, parse_mode='Markdown')
        
    elif query.data == "settings":
        await notifications_command(update, context)
        
    elif query.data == "toggle_notifications":
        await notifications_command(update, context)


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Произошла ошибка: {context.error}")


def main():
    """Запуск бота"""
    # Получаем токен бота из настроек Django
    BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
    
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не установлен в настройках!")
        return
    
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрируем обработчики команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("notifications", notifications_command))
    
    # Обработчик callback-кнопок
    from telegram.ext import CallbackQueryHandler
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # Обработчик ошибок
    application.add_error_handler(error_handler)
    
    # Запускаем бота
    logger.info("Бот запущен и готов к работе!")
    
    # Исправление для Python 3.14: создаем event loop явно перед запуском
    # В Python 3.14 asyncio.get_event_loop() больше не создает loop автоматически
    try:
        if sys.version_info >= (3, 14):
            # Для Python 3.14+ создаем новый event loop и устанавливаем его как текущий
            try:
                # Пытаемся получить существующий loop
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    # Если loop закрыт, создаем новый
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError:
                # Если нет текущего loop, создаем новый
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        else:
            loop = None
        
        # Запускаем бота
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except KeyboardInterrupt:
        logger.info("Остановка бота...")
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise


if __name__ == '__main__':
    main()

