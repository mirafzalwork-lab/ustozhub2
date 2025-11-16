#!/usr/bin/env python3
"""
Компиляция .po файлов в .mo файлы используя Python (без внешних зависимостей)
"""

import os
import struct
from pathlib import Path

class POParser:
    """Парсер для .po файлов"""
    
    def __init__(self, po_file):
        self.po_file = po_file
        self.messages = {}  # {msgid: msgstr}
        self.parse()
    
    def parse(self):
        """Парсит .po файл"""
        with open(self.po_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        current_msgid = None
        current_msgstr = None
        in_msgid = False
        in_msgstr = False
        
        for line in lines:
            line = line.rstrip('\n')
            
            # Пропускаем комментарии и пустые строки
            if line.startswith('#') or not line.strip():
                if current_msgid is not None and current_msgstr is not None:
                    self.messages[current_msgid] = current_msgstr
                    current_msgid = None
                    current_msgstr = None
                in_msgid = False
                in_msgstr = False
                continue
            
            # Парсим msgid
            if line.startswith('msgid '):
                if current_msgid is not None and current_msgstr is not None:
                    self.messages[current_msgid] = current_msgstr
                
                current_msgid = self._extract_string(line)
                in_msgid = True
                in_msgstr = False
                current_msgstr = None
            
            # Парсим msgstr
            elif line.startswith('msgstr '):
                current_msgstr = self._extract_string(line)
                in_msgid = False
                in_msgstr = True
            
            # Продолжение многострочных строк
            elif (in_msgid or in_msgstr) and line.startswith('"'):
                string_val = self._extract_string(line)
                if in_msgid:
                    current_msgid += string_val
                else:
                    if current_msgstr is None:
                        current_msgstr = string_val
                    else:
                        current_msgstr += string_val
        
        # Добавляем последнее сообщение
        if current_msgid is not None and current_msgstr is not None:
            self.messages[current_msgid] = current_msgstr
    
    def _extract_string(self, line):
        """Извлекает строку из строки msgid/msgstr"""
        # Удаляем msgid/msgstr префикс
        line = line.split(' ', 1)[1] if ' ' in line else ''
        
        # Извлекаем строку между кавычками
        if line.startswith('"') and line.endswith('"'):
            return line[1:-1]
        elif line.startswith("'") and line.endswith("'"):
            return line[1:-1]
        return ''

class MOGenerator:
    """Генератор .mo файлов"""
    
    def __init__(self, messages):
        self.messages = messages
    
    def generate(self, mo_file):
        """Генерирует .mo файл"""
        # Отфильтровываем пустые msgid (заголовки)
        messages = {k: v for k, v in self.messages.items() if k}
        
        if not messages:
            print(f"⚠️  Нет сообщений для компиляции в {mo_file}")
            return
        
        # Сортируем сообщения для лучшего распределения
        sorted_messages = sorted(messages.items())
        
        # Собираем данные
        ids = []
        strs = []
        offsets = []
        
        current_offset = 7 * 4 + 16 * len(sorted_messages)
        
        for msgid, msgstr in sorted_messages:
            msgid_bytes = msgid.encode('utf-8')
            msgstr_bytes = msgstr.encode('utf-8')
            
            ids.append((len(msgid_bytes), current_offset))
            current_offset += len(msgid_bytes) + 1
            
            strs.append((len(msgstr_bytes), current_offset))
            current_offset += len(msgstr_bytes) + 1
        
        # Собираем .mo файл
        with open(mo_file, 'wb') as f:
            # Заголовок
            f.write(struct.pack('Iiiiiii',
                0xde120495,  # Магическое число
                0,           # Версия
                len(sorted_messages),  # Количество строк
                7 * 4,       # Смещение таблицы исходных строк
                7 * 4 + 8 * len(sorted_messages),  # Смещение таблицы переводов
                0,           # Размер хэш-таблицы
                0            # Смещение хэш-таблицы
            ))
            
            # Таблица исходных строк
            for length, offset in ids:
                f.write(struct.pack('II', length, offset))
            
            # Таблица переводов
            for length, offset in strs:
                f.write(struct.pack('II', length, offset))
            
            # Строки
            for msgid, msgstr in sorted_messages:
                f.write(msgid.encode('utf-8'))
                f.write(b'\x00')
                f.write(msgstr.encode('utf-8'))
                f.write(b'\x00')

def compile_translations():
    """Компилирует все .po файлы"""
    
    BASE_DIR = Path(__file__).parent
    LOCALE_DIR = BASE_DIR / 'locale'
    LANGUAGES = ['ru', 'uz', 'en']
    
    print("🌍 Компиляция переводов (Python)...\n")
    
    for lang in LANGUAGES:
        po_file = LOCALE_DIR / lang / 'LC_MESSAGES' / 'django.po'
        mo_file = LOCALE_DIR / lang / 'LC_MESSAGES' / 'django.mo'
        
        if not po_file.exists():
            print(f"⚠️  {lang}: Файл не найден - {po_file}")
            continue
        
        try:
            print(f"📝 Компилирование {lang}...")
            
            # Парсим .po файл
            parser = POParser(po_file)
            
            # Генерируем .mo файл
            generator = MOGenerator(parser.messages)
            generator.generate(mo_file)
            
            print(f"✅ {lang}: Готово!")
            print(f"   📦 Сообщений: {len(parser.messages)}")
            print(f"   📄 Файл: {mo_file}\n")
            
        except Exception as e:
            print(f"❌ {lang}: Ошибка - {e}\n")
    
    print("✨ Все переводы скомпилированы!")

if __name__ == '__main__':
    compile_translations()

