"""
Core package init.

Импорт Celery app здесь нужен, чтобы @shared_task в приложениях
автоматически использовал нашу конфигурацию (см. core/celery.py).
Делается через try/except — чтобы dev-окружение без установленного celery
не падало (Celery опциональна на первых фазах разработки).
"""
try:
    from .celery import app as celery_app
    __all__ = ('celery_app',)
except ImportError:
    # celery ещё не установлен — это нормально для dev до Phase 4
    pass
