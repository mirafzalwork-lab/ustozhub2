#!/bin/bash
# Скрипт для быстрой настройки системы сообщений платформы

echo "🚀 Настройка системы сообщений платформы UstozHub..."

# Переходим в директорию проекта
cd /Users/humoyunswe/Desktop/Projects/ustozhubuz

echo "📋 Применяем миграции..."
python3 manage.py migrate teachers

echo "📝 Создаем тестовые сообщения..."
python3 manage.py create_platform_messages

echo "🔧 Проверяем Django..."
python3 manage.py check

echo "✅ Готово! Теперь запустите сервер:"
echo "python3 manage.py runserver"
echo ""
echo "Откройте браузер и проверьте навбар - должны появиться уведомления с иконкой рупора 📢"