"""
Патч для исправления ошибки совместимости Django 4.2 с Python 3.14
ВРЕМЕННОЕ РЕШЕНИЕ - рекомендуется использовать Python 3.11 или 3.12

Проблема: Python 3.14 изменил поведение super().__copy__(), что вызывает
ошибку в django/template/context.py при копировании контекста шаблонов.

Использование: Импортируйте этот модуль ДО импорта Django
"""
import sys

def apply_patch():
    """Применяет патч для совместимости с Python 3.14"""
    if sys.version_info >= (3, 14):
        try:
            import django.template.context as context_module
            
            # Получаем класс Context
            Context = context_module.Context
            
            # Сохраняем оригинальный метод если нужно для отладки
            _original_copy = Context.__copy__
            
            def patched_copy(self):
                """
                Исправленная версия __copy__ для совместимости с Python 3.14
                Обходит проблему с super().__copy__() в Python 3.14
                
                Оригинальный код Django пытается вызвать:
                    duplicate = super().__copy__()
                    duplicate.dicts = self.dicts[:]
                
                Но в Python 3.14 super().__copy__() возвращает объект без __dict__
                """
                # Создаем новый экземпляр напрямую
                duplicate = self.__class__.__new__(self.__class__)
                
                # Инициализируем dicts напрямую (обходя super())
                # dicts - это список словарей контекста
                try:
                    duplicate.dicts = list(self.dicts) if hasattr(self, 'dicts') else []
                except (AttributeError, TypeError):
                    duplicate.dicts = []
                
                # Копируем все остальные атрибуты из исходного объекта
                # Используем vars() для получения всех атрибутов
                for key, value in vars(self).items():
                    if key != 'dicts':
                        try:
                            setattr(duplicate, key, value)
                        except (AttributeError, TypeError):
                            # Пропускаем атрибуты, которые нельзя установить
                            pass
                
                return duplicate
            
            # Применяем патч
            Context.__copy__ = patched_copy
            
            # Также патчим RequestContext если он существует
            if hasattr(context_module, 'RequestContext'):
                RequestContext = context_module.RequestContext
                RequestContext.__copy__ = patched_copy
            
            return True
            
        except ImportError:
            # Django еще не импортирован
            return False
        except Exception as e:
            # Если патч не удался, продолжаем работу без него
            print(f"⚠️ Не удалось применить патч Django для Python 3.14: {e}", file=sys.stderr)
            return False
    
    return False

# Автоматически применяем патч при импорте модуля (если Django уже загружен)
# Если Django еще не загружен, патч будет применен позже через manage.py
try:
    import django
    if django.VERSION:
        _ = apply_patch()
except ImportError:
    pass  # Django еще не установлен

