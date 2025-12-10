#!/usr/bin/env python3
"""
Скрипт для компиляции переводов (файлы .po в .mo)
Используется когда Django не установлен в виртуальном окружении
"""

import os
import sys
import subprocess
from pathlib import Path

# Найдем базовый каталог проекта
BASE_DIR = Path(__file__).parent

# Каталог с переводами
LOCALE_DIR = BASE_DIR / 'locale'

# Поддерживаемые языки
LANGUAGES = ['ru', 'uz', 'en']

def compile_messages():
    """Компилирует .po файлы в .mo файлы"""
    
    print("🌍 Начинается компиляция переводов...")
    print(f"📂 Каталог переводов: {LOCALE_DIR}\n")
    
    for lang in LANGUAGES:
        po_file = LOCALE_DIR / lang / 'LC_MESSAGES' / 'django.po'
        mo_file = LOCALE_DIR / lang / 'LC_MESSAGES' / 'django.mo'
        
        if not po_file.exists():
            print(f"⚠️  Файл {lang} не найден: {po_file}")
            continue
        
        print(f"📝 Компилирование: {lang}...")
        
        try:
            # Используем msgfmt для компиляции
            result = subprocess.run(
                ['msgfmt', '-o', str(mo_file), str(po_file)],
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                print(f"✅ {lang}: успешно скомпилировано")
                print(f"   📦 {mo_file}\n")
            else:
                print(f"❌ {lang}: ошибка компиляции")
                print(f"   Stderr: {result.stderr}\n")
                
        except FileNotFoundError:
            print(f"⚠️  msgfmt не найден. Установите gettext:")
            print(f"   На macOS: brew install gettext")
            print(f"   На Linux: sudo apt-get install gettext\n")
            return False
    
    print("✨ Компиляция завершена!")
    return True

if __name__ == '__main__':
    success = compile_messages()
    sys.exit(0 if success else 1)

