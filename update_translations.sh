#!/bin/bash

# 🌍 Скрипт для обновления и компиляции переводов

set -e  # Выход при ошибке

echo "🔄 Обновление переводов для UstozHub..."
echo ""

# Шаг 1: Пересобрать сообщения
echo "📝 Шаг 1: Сканирование кода и обновление .po файлов..."
python manage.py makemessages -l ru -l uz -l en --verbosity 2

echo ""
echo "✅ Файлы обновлены:"
echo "   - locale/ru/LC_MESSAGES/django.po"
echo "   - locale/uz/LC_MESSAGES/django.po"
echo "   - locale/en/LC_MESSAGES/django.po"
echo ""
echo "📌 ВАЖНО: Отредактируйте файлы выше и заполните пустые msgstr!"
echo ""

# Шаг 2: Скомпилировать
echo "🔨 Шаг 2: Компиляция переводов..."
python manage.py compilemessages --verbosity 2

echo ""
echo "✅ Переводы скомпилированы:"
echo "   - locale/ru/LC_MESSAGES/django.mo"
echo "   - locale/uz/LC_MESSAGES/django.mo"
echo "   - locale/en/LC_MESSAGES/django.mo"
echo ""
echo "🎉 Готово! Протестируйте переводы:"
echo "   - http://127.0.0.1:8000/ru/..."
echo "   - http://127.0.0.1:8000/uz/..."
echo "   - http://127.0.0.1:8000/en/..."

