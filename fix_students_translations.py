#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Скрипт для исправления переводов страницы students_list.html
"""

import os

# Переводы для узбекского языка
UZ_TRANSLATIONS = {
    "Ученики ищут учителей": "O'quvchilar o'qituvchi izlayapti",
    "Найдите учеников, которым нужна ваша помощь. Свяжитесь с ними напрямую и начните преподавать!": "O'quvchilarni toping va ularga yordam bering. Ular bilan to'g'ridan-to'g'ri bog'laning va dars berishni boshlang!",
    "Имя ученика...": "O'quvchi ismi...",
    "Предмет для изучения": "O'rganish uchun fan",
    "Бюджет ученика (сум/час)": "O'quvchi byudjeti (so'm/soat)",
}

# Переводы для английского языка
EN_TRANSLATIONS = {
    "Ученики ищут учителей": "Students Looking for Teachers",
    "Найдите учеников, которым нужна ваша помощь. Свяжитесь с ними напрямую и начните преподавать!": "Find students who need your help. Contact them directly and start teaching!",
    "Имя ученика...": "Student name...",
    "Предмет для изучения": "Subject to Learn",
    "Бюджет ученика (сум/час)": "Student Budget (UZS/hour)",
}

def fix_po_file(filepath, translations):
    """Исправляет переводы в .po файле"""
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Пропускаем fuzzy метки
        if line.strip().startswith('#, fuzzy'):
            i += 1
            continue
        
        # Пропускаем старые msgid метки
        if line.strip().startswith('#| msgid'):
            i += 1
            continue
        
        # Обрабатываем msgid
        if line.startswith('msgid '):
            new_lines.append(line)
            
            # Извлекаем текст msgid
            msgid_text = line[7:-2] if line.endswith('"\n') else line[7:]
            
            # Проверяем многострочный msgid
            i += 1
            full_msgid = msgid_text
            while i < len(lines) and lines[i].startswith('"'):
                full_msgid += lines[i][1:-2]
                new_lines.append(lines[i])
                i += 1
            
            # Если это msgstr и у нас есть перевод
            if i < len(lines) and lines[i].startswith('msgstr '):
                msgstr_line = lines[i]
                
                # Проверяем, есть ли перевод для этого msgid
                if full_msgid in translations:
                    # Заменяем пустой или неправильный перевод
                    new_lines.append(f'msgstr "{translations[full_msgid]}"\n')
                    i += 1
                    # Пропускаем многострочный msgstr если есть
                    while i < len(lines) and lines[i].startswith('"'):
                        i += 1
                else:
                    new_lines.append(msgstr_line)
                    i += 1
            continue
        
        new_lines.append(line)
        i += 1
    
    # Записываем обратно
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    
    print(f"✅ Исправлен файл: {filepath}")

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Исправляем узбекский
    uz_po = os.path.join(base_dir, 'locale/uz/LC_MESSAGES/django.po')
    if os.path.exists(uz_po):
        print("🔧 Исправление узбекских переводов...")
        fix_po_file(uz_po, UZ_TRANSLATIONS)
    
    # Исправляем английский
    en_po = os.path.join(base_dir, 'locale/en/LC_MESSAGES/django.po')
    if os.path.exists(en_po):
        print("🔧 Исправление английских переводов...")
        fix_po_file(en_po, EN_TRANSLATIONS)
    
    print("\n✅ Все переводы исправлены!")
    print("\n📝 Следующий шаг: запустите компиляцию переводов:")
    print("   pipenv run python manage.py compilemessages")
