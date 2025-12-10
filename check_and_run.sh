#!/bin/bash
echo "🚀 Проверка системы уведомлений UstozHub..."

cd /Users/humoyunswe/Desktop/Projects/ustozhubuz

echo "1️⃣ Применяем миграции..."
python3 manage.py migrate teachers

echo "2️⃣ Создаем тестовые сообщения..."
python3 manage.py create_platform_messages

echo "3️⃣ Запускаем сервер на порту 8001..."
echo "Откройте http://127.0.0.1:8001 в браузере"
echo "В навбаре должна появиться оранжевая иконка рупора 📢"
echo ""
echo "Нажмите Ctrl+C для остановки сервера"

python3 manage.py runserver 8001