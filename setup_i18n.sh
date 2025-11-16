#!/bin/bash

# Настройка мультиязычности для UstozHub
# Поддержка: Русский (ru), Узбекский (uz), Английский (en)

set -e

echo "🌍 Настройка мультиязычности..."
echo ""

# Шаг 1: Пересобрать сообщения
echo "📝 Обновление переводов из кода..."
python manage.py makemessages -l ru -l uz -l en

echo ""
echo "✅ Файлы обновлены:"
echo "   ✓ locale/ru/LC_MESSAGES/django.po"
echo "   ✓ locale/uz/LC_MESSAGES/django.po"
echo "   ✓ locale/en/LC_MESSAGES/django.po"
echo ""

# Шаг 2: Скомпилировать
echo "🔨 Компиляция переводов..."
python manage.py compilemessages

echo ""
echo "✅ Переводы скомпилированы:"
echo "   ✓ locale/ru/LC_MESSAGES/django.mo"
echo "   ✓ locale/uz/LC_MESSAGES/django.mo"
echo "   ✓ locale/en/LC_MESSAGES/django.mo"
echo ""

echo "✨ Готово!"
echo ""
echo "Тестируйте на разных языках:"
echo "  🇷🇺 Русский:   http://127.0.0.1:8000/ru/"
echo "  🇺🇿 Узбекский: http://127.0.0.1:8000/uz/"
echo "  🇬🇧 English:   http://127.0.0.1:8000/en/"

