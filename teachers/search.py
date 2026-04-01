"""
Модуль умного поиска для UstozHub.

Обеспечивает:
- Нормализацию поисковых запросов
- Расширение запросов через словарь синонимов (RU/EN/UZ)
- Построение Q-объектов для Django ORM
"""

import re
from django.db.models import Q, Case, When, Value, IntegerField, F, Max
from django.db.models.functions import Coalesce


# =============================================================================
# СЛОВАРЬ СИНОНИМОВ
# Ключ — нормализованное слово (lowercase), значение — список альтернатив.
# Расширяется простым добавлением записей.
# =============================================================================

SUBJECT_SYNONYMS = {
    # ── Английский язык ──
    "eng": ["английский", "english", "ingliz"],
    "англ": ["английский", "english", "ingliz"],
    "english": ["английский", "ingliz", "англ"],
    "инглиш": ["английский", "english", "ingliz"],
    "ingliz": ["английский", "english"],
    "английский": ["english", "ingliz"],

    # ── Русский язык ──
    "рус": ["русский", "russian", "rus"],
    "rus": ["русский", "russian"],
    "russ": ["русский", "russian", "rus"],
    "russian": ["русский", "rus"],
    "русский": ["russian", "rus"],

    # ── Узбекский язык ──
    "узб": ["узбекский", "uzbek", "o'zbek"],
    "uzbek": ["узбекский", "o'zbek"],
    "ozbek": ["узбекский", "uzbek", "o'zbek"],
    "o'zbek": ["узбекский", "uzbek"],
    "узбекский": ["uzbek", "o'zbek"],

    # ── Математика ──
    "мат": ["математика", "math", "matematika"],
    "матем": ["математика", "math", "matematika"],
    "math": ["математика", "matematika"],
    "maths": ["математика", "math", "matematika"],
    "mathematics": ["математика", "math", "matematika"],
    "matematika": ["математика", "math"],
    "математика": ["math", "matematika"],

    # ── Физика ──
    "физ": ["физика", "physics", "fizika"],
    "physics": ["физика", "fizika"],
    "fizika": ["физика", "physics"],
    "физика": ["physics", "fizika"],

    # ── Химия ──
    "хим": ["химия", "chemistry", "kimyo"],
    "chem": ["химия", "chemistry", "kimyo"],
    "chemistry": ["химия", "kimyo"],
    "kimyo": ["химия", "chemistry"],
    "химия": ["chemistry", "kimyo"],

    # ── Биология ──
    "био": ["биология", "biology", "biologiya"],
    "bio": ["биология", "biology", "biologiya"],
    "biology": ["биология", "biologiya"],
    "biologiya": ["биология", "biology"],
    "биология": ["biology", "biologiya"],

    # ── История ──
    "ист": ["история", "history", "tarix"],
    "history": ["история", "tarix"],
    "tarix": ["история", "history"],
    "история": ["history", "tarix"],

    # ── География ──
    "гео": ["география", "geography", "geografiya"],
    "geography": ["география", "geografiya"],
    "geografiya": ["география", "geography"],
    "география": ["geography", "geografiya"],

    # ── Информатика / Программирование ──
    "инф": ["информатика", "informatika", "programming"],
    "информатика": ["informatika", "programming"],
    "informatika": ["информатика", "programming"],
    "прог": ["программирование", "programming", "dasturlash"],
    "programming": ["программирование", "dasturlash"],
    "dasturlash": ["программирование", "programming"],
    "программирование": ["programming", "dasturlash"],

    # ── Литература ──
    "лит": ["литература", "literature", "adabiyot"],
    "literature": ["литература", "adabiyot"],
    "adabiyot": ["литература", "literature"],
    "литература": ["literature", "adabiyot"],

    # ── Немецкий язык ──
    "нем": ["немецкий", "german", "nemis"],
    "german": ["немецкий", "nemis"],
    "deutsch": ["немецкий", "german", "nemis"],
    "nemis": ["немецкий", "german"],
    "немецкий": ["german", "nemis"],

    # ── Французский язык ──
    "фр": ["французский", "french", "fransuz"],
    "франц": ["французский", "french", "fransuz"],
    "french": ["французский", "fransuz"],
    "fransuz": ["французский", "french"],
    "французский": ["french", "fransuz"],

    # ── Арабский язык ──
    "араб": ["арабский", "arabic", "arab"],
    "arabic": ["арабский", "arab"],
    "арабский": ["arabic", "arab"],

    # ── Китайский язык ──
    "кит": ["китайский", "chinese", "xitoy"],
    "chinese": ["китайский", "xitoy"],
    "xitoy": ["китайский", "chinese"],
    "китайский": ["chinese", "xitoy"],

    # ── Корейский язык ──
    "кор": ["корейский", "korean", "koreys"],
    "korean": ["корейский", "koreys"],
    "корейский": ["korean", "koreys"],

    # ── Турецкий язык ──
    "тур": ["турецкий", "turkish", "turk"],
    "turkish": ["турецкий", "turk"],
    "турецкий": ["turkish", "turk"],

    # ── Экономика ──
    "эконом": ["экономика", "economics", "iqtisod"],
    "economics": ["экономика", "iqtisod"],
    "iqtisod": ["экономика", "economics"],
    "экономика": ["economics", "iqtisod"],

    # ── Музыка ──
    "муз": ["музыка", "music", "musiqa"],
    "music": ["музыка", "musiqa"],
    "musiqa": ["музыка", "music"],
    "музыка": ["music", "musiqa"],

    # ── IELTS / TOEFL ──
    "ielts": ["ielts", "английский"],
    "toefl": ["toefl", "английский"],
    "айелтс": ["ielts", "английский"],

    # ── SAT ──
    "sat": ["sat", "математика", "английский"],
    "сат": ["sat"],

    # ── Общие сокращения «язык» ──
    "яз": ["язык"],
    "lang": ["язык", "language"],
    "til": ["тили", "язык"],
}

# Построим обратный индекс для быстрого поиска по подстрокам
_SYNONYM_KEYS_SORTED = sorted(SUBJECT_SYNONYMS.keys(), key=len, reverse=True)


def normalize_query(query: str) -> str:
    """Нормализует поисковый запрос: lowercase, trim, убрать лишние пробелы."""
    if not query:
        return ""
    q = query.strip().lower()
    q = re.sub(r'\s+', ' ', q)
    return q


def expand_query(query: str) -> list[str]:
    """
    Расширяет запрос через словарь синонимов.
    Возвращает список альтернативных написаний (без дубликатов).
    Оригинальный запрос НЕ включается — он обрабатывается отдельно.
    """
    q = normalize_query(query)
    if not q:
        return []

    expansions = set()

    # Проверяем весь запрос целиком
    if q in SUBJECT_SYNONYMS:
        expansions.update(SUBJECT_SYNONYMS[q])

    # Проверяем каждое слово запроса отдельно
    words = q.split()
    for word in words:
        if word in SUBJECT_SYNONYMS:
            expansions.update(SUBJECT_SYNONYMS[word])

    # Убираем оригинальный запрос из расширений
    expansions.discard(q)

    return list(expansions)


def build_subject_search_q(query: str) -> Q:
    """
    Строит Q-объект для поиска предметов (Subject).
    Ищет по name и description с учётом синонимов.
    """
    q = normalize_query(query)
    if not q:
        return Q()

    conditions = Q(name__icontains=q) | Q(description__icontains=q)

    for synonym in expand_query(q):
        conditions |= Q(name__icontains=synonym) | Q(description__icontains=synonym)

    return conditions


def build_teacher_search_q(query: str) -> Q:
    """
    Строит Q-объект для поиска учителей (TeacherProfile).
    Ищет по предметам, имени/фамилии, bio — с учётом синонимов.
    """
    q = normalize_query(query)
    if not q:
        return Q()

    # Базовый поиск по оригинальному запросу
    conditions = (
        Q(subjects__name__icontains=q) |
        Q(user__first_name__icontains=q) |
        Q(user__last_name__icontains=q) |
        Q(bio__icontains=q)
    )

    # Расширенный поиск по синонимам (только предметы и bio)
    for synonym in expand_query(q):
        conditions |= (
            Q(subjects__name__icontains=synonym) |
            Q(bio__icontains=synonym)
        )

    return conditions


def build_teacher_relevance_annotations(query: str):
    """
    Возвращает аннотации релевантности для ранжирования учителей.
    Учитывает как оригинальный запрос, так и синонимы.

    Returns:
        tuple: (subject_rank, name_rank, bio_rank) — аннотации для annotate()
    """
    q = normalize_query(query)
    synonyms = expand_query(q)

    # --- Subject rank ---
    subject_whens = [
        When(subjects__name__iexact=q, then=Value(100)),
        When(subjects__name__istartswith=q, then=Value(90)),
        When(subjects__name__icontains=q, then=Value(80)),
    ]
    # Синонимы получают чуть меньший приоритет
    for syn in synonyms:
        subject_whens.append(When(subjects__name__iexact=syn, then=Value(75)))
        subject_whens.append(When(subjects__name__istartswith=syn, then=Value(70)))
        subject_whens.append(When(subjects__name__icontains=syn, then=Value(65)))

    subject_rank = Coalesce(
        Max(Case(*subject_whens, default=Value(0), output_field=IntegerField())),
        Value(0),
    )

    # --- Name rank (только оригинальный запрос — синонимы для имён не нужны) ---
    name_rank = Coalesce(
        Max(Case(
            When(user__first_name__iexact=q, then=Value(70)),
            When(user__last_name__iexact=q, then=Value(70)),
            When(user__first_name__istartswith=q, then=Value(60)),
            When(user__last_name__istartswith=q, then=Value(60)),
            When(user__first_name__icontains=q, then=Value(50)),
            When(user__last_name__icontains=q, then=Value(50)),
            default=Value(0),
            output_field=IntegerField(),
        )),
        Value(0),
    )

    # --- Bio rank ---
    bio_whens = [When(bio__icontains=q, then=Value(40))]
    for syn in synonyms:
        bio_whens.append(When(bio__icontains=syn, then=Value(30)))

    bio_rank = Case(*bio_whens, default=Value(0), output_field=IntegerField())

    return subject_rank, name_rank, bio_rank


def build_subject_relevance_annotation(query: str):
    """
    Возвращает аннотацию релевантности для ранжирования предметов (Subject).
    Используется в subjects_autocomplete().
    """
    q = normalize_query(query)
    synonyms = expand_query(q)

    whens = [
        When(name__iexact=q, then=Value(6)),
        When(name__istartswith=q, then=Value(5)),
        When(name__icontains=q, then=Value(4)),
        When(description__icontains=q, then=Value(3)),
    ]

    for syn in synonyms:
        whens.append(When(name__iexact=syn, then=Value(4)))
        whens.append(When(name__istartswith=syn, then=Value(3)))
        whens.append(When(name__icontains=syn, then=Value(2)))
        whens.append(When(description__icontains=syn, then=Value(1)))

    return Case(*whens, default=Value(0), output_field=IntegerField())


def build_student_search_q(query: str) -> Q:
    """
    Строит Q-объект для поиска студентов (StudentProfile).
    Ищет по имени, описанию, bio — с учётом синонимов.
    """
    q = normalize_query(query)
    if not q:
        return Q()

    conditions = (
        Q(user__first_name__icontains=q) |
        Q(user__last_name__icontains=q) |
        Q(description__icontains=q) |
        Q(bio__icontains=q)
    )

    for synonym in expand_query(q):
        conditions |= (
            Q(description__icontains=synonym) |
            Q(bio__icontains=synonym)
        )

    return conditions
