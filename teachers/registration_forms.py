"""
Professional multi-step teacher registration forms
Clean, user-friendly forms with proper validation and helper texts
"""
from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from .models import User, TeacherProfile, Subject, City, Certificate, TeacherSubject
import re
import logging
import mimetypes

logger = logging.getLogger(__name__)


class Step1BasicProfileForm(forms.Form):
    """
    STEP 1: Basic Profile Information
    Fields: Profile photo, First name, Last name, Gender, Teaching languages, Phone
    """
    GENDER_CHOICES = [
        ('', 'Не указывать'),
        ('male', 'Мужской'),
        ('female', 'Женский'),
    ]
    
    avatar = forms.ImageField(
        required=False,
        label='Фото профиля',
        widget=forms.FileInput(attrs={
            'class': 'form-file-input',
            'accept': 'image/jpeg,image/jpg,image/png',
            'id': 'id_avatar'
        }),
        help_text='JPG, PNG. Максимум 5 МБ. Рекомендуемый размер: 300×300 px'
    )
    
    first_name = forms.CharField(
        max_length=150,
        required=True,
        label='Имя',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Введите ваше имя',
            'autocomplete': 'given-name'
        }),
        help_text='Имя, которое увидят ученики'
    )
    
    last_name = forms.CharField(
        max_length=150,
        required=True,
        label='Фамилия',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Введите вашу фамилию',
            'autocomplete': 'family-name'
        }),
        help_text='Фамилия для профиля'
    )
    
    def clean_first_name(self):
        """✅ Валидация имени - очистка и проверка"""
        first_name = self.cleaned_data.get('first_name')
        if first_name:
            first_name = first_name.strip()
            # ✅ Проверяем на пустое значение после очистки
            if not first_name:
                raise ValidationError('Имя не может быть пустым')
            # ✅ Проверяем на спецсимволы (разрешены буквы, пробелы, дефисы, апострофы)
            if not re.match(r"^[\w\s\-'а-яё]+$", first_name, re.IGNORECASE | re.UNICODE):
                raise ValidationError('Имя содержит недопустимые символы')
        return first_name
    
    def clean_last_name(self):
        """✅ Валидация фамилии - очистка и проверка"""
        last_name = self.cleaned_data.get('last_name')
        if last_name:
            last_name = last_name.strip()
            if not last_name:
                raise ValidationError('Фамилия не может быть пустой')
            if not re.match(r"^[\w\s\-'а-яё]+$", last_name, re.IGNORECASE | re.UNICODE):
                raise ValidationError('Фамилия содержит недопустимые символы')
        return last_name
    
    gender = forms.ChoiceField(
        choices=GENDER_CHOICES,
        required=False,
        label='Пол',
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        help_text='Необязательно. Помогает ученикам найти подходящего учителя.',
    )
    
    teaching_languages = forms.MultipleChoiceField(
        choices=TeacherProfile.TEACHING_LANGUAGES,
        required=True,
        label='Языки преподавания',
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'teaching-language-checkbox'
        }),
        help_text='Выберите языки, на которых вы проводите занятия'
    )
    
    phone = forms.CharField(
        max_length=20,
        required=True,
        label='Номер телефона',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '+998 90 123 45 67',
            'autocomplete': 'tel',
            'inputmode': 'tel',
        }),
        help_text='Международный формат с кодом страны. Например: +998 90 123 45 67 или +1 202 555 0143.'
    )
    
    def clean_avatar(self):
        """✅ Валидация аватара с проверкой размера и типа"""
        avatar = self.cleaned_data.get('avatar')
        if avatar:
            try:
                # ✅ Проверка размера файла (5 МБ максимум)
                if avatar.size > 5 * 1024 * 1024:
                    raise ValidationError('Размер файла не должен превышать 5 МБ')
                
                # ✅ Проверка расширения файла
                valid_extensions = ['.jpg', '.jpeg', '.png']
                ext = avatar.name.lower().split('.')[-1]
                if f'.{ext}' not in valid_extensions:
                    raise ValidationError('Разрешены только JPG, JPEG и PNG форматы')
                
                # ✅ Проверка MIME type для безопасности
                mime_type, _ = mimetypes.guess_type(avatar.name)
                if mime_type not in ['image/jpeg', 'image/png']:
                    logger.warning(f"Avatar upload: недопустимый MIME type - {mime_type}")
                    raise ValidationError('Недопустимый тип изображения')
            
            except ValidationError:
                raise
            except Exception as e:
                logger.error(f"Avatar validation error: {e}", exc_info=True)
                raise ValidationError('Ошибка при проверке файла')
        
        return avatar
    
    def clean_phone(self):
        """Валидация телефона в международном формате E.164.

        E.164 = '+' + 8…15 цифр (страна + номер). Подходит и для +998 (Узбекистан),
        и для иностранных номеров (диаспора, иностранные учителя).
        Пробелы/дефисы/скобки разрешены при вводе — мы их вырезаем.
        """
        phone = self.cleaned_data.get('phone')
        if not phone:
            return phone
        try:
            phone = phone.strip()
            phone_digits = re.sub(r'[\s\-()]', '', phone)
            if not re.match(r'^\+\d{8,15}$', phone_digits):
                logger.warning(f"Invalid phone format: {phone}")
                raise ValidationError(
                    'Введите корректный номер в международном формате с кодом страны: '
                    '«+» и 8–15 цифр (например, +998 90 123 45 67).'
                )
            # Сохраняем в нормализованном виде (без пробелов/дефисов).
            return phone_digits
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Phone validation error: {e}")
            raise ValidationError('Ошибка при проверке номера телефона')
    
    def clean_teaching_languages(self):
        # Получаем значение (должно быть список от CheckboxSelectMultiple)
        languages = self.cleaned_data.get('teaching_languages')
        
        if not languages or len(languages) == 0:
            raise ValidationError('Выберите хотя бы один язык преподавания')
        
        return languages


class Step2AccountSecurityForm(UserCreationForm):
    """
    STEP 1.5: Account Security
    Fields: Username, Password, Confirm Password
    """
    username = forms.CharField(
        max_length=150,
        required=True,
        label='Имя пользователя',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'john_teacher',
            'autocomplete': 'username'
        }),
        help_text='Используйте буквы, цифры и символы _ . -'
    )
    
    password1 = forms.CharField(
        label='Пароль',
        widget=forms.PasswordInput(attrs={
            'class': 'form-input',
            'placeholder': 'Введите надежный пароль',
            'autocomplete': 'new-password'
        }),
        help_text='Минимум 8 символов. Используйте буквы и цифры.'
    )
    
    password2 = forms.CharField(
        label='Подтвердите пароль',
        widget=forms.PasswordInput(attrs={
            'class': 'form-input',
            'placeholder': 'Повторите пароль',
            'autocomplete': 'new-password'
        }),
        help_text='Введите тот же пароль для подтверждения'
    )
    
    email = forms.EmailField(
        required=True,
        label='Email',
        widget=forms.EmailInput(attrs={
            'class': 'form-input',
            'placeholder': 'your.email@example.com',
            'autocomplete': 'email'
        }),
        help_text='Используется для входа и восстановления доступа к аккаунту'
    )
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']
    
    def clean_username(self):
        """✅ Валидация username с проверкой уникальности"""
        username = self.cleaned_data.get('username')
        if username:
            try:
                username = username.strip()
                # ✅ Проверяем, что username содержит только разрешенные символы
                if not re.match(r'^[\w.-]+$', username):
                    raise ValidationError('Используйте только буквы, цифры и символы _ . -')
                
                # ✅ Проверяем уникальность
                if User.objects.filter(username__iexact=username).exists():
                    logger.warning(f"Registration: username уже используется - {username}")
                    raise ValidationError('Это имя пользователя уже занято')
            except ValidationError:
                raise
            except Exception as e:
                logger.error(f"Username validation error: {e}", exc_info=True)
                raise ValidationError('Ошибка при проверке имени пользователя')
        
        return username
    
    def clean_email(self):
        """✅ Валидация email с проверкой уникальности"""
        email = self.cleaned_data.get('email')
        if email:
            try:
                email = email.strip().lower()
                # ✅ Проверяем уникальность
                if User.objects.filter(email__iexact=email).exists():
                    logger.warning(f"Registration: email уже используется - {email}")
                    raise ValidationError('Этот email уже используется')
            except ValidationError:
                raise
            except Exception as e:
                logger.error(f"Email validation error: {e}", exc_info=True)
                raise ValidationError('Ошибка при проверке email')
        return email

    def clean_password1(self):
        """Allow empty password for Google users."""
        password1 = self.cleaned_data.get('password1')
        if not self.fields['password1'].required and not password1:
            return password1
        return super().clean_password1() if hasattr(super(), 'clean_password1') else password1

    def clean_password2(self):
        """Allow empty password for Google users."""
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        # If password is optional (Google user) and both are empty — skip validation
        if not self.fields['password1'].required and not password1 and not password2:
            return password2
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError('Пароли не совпадают.')
        return password2


class Step3EducationExperienceForm(forms.Form):
    """
    STEP 2: Education & Experience
    Fields: Education level, Institution, Work experience, About me
    """
    education_level = forms.ChoiceField(
        choices=[('', 'Выберите уровень образования')] + list(TeacherProfile.EDUCATION_LEVELS),
        required=False,
        label='Уровень образования',
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        help_text='Выберите ваш наивысший уровень образования (желательно)'
    )
    
    university = forms.CharField(
        max_length=200,
        required=False,
        label='Учебное заведение',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Например: НУУз, ТГЭУ, Вестминстер...'
        }),
        help_text='Полное или краткое название вашего университета/института (желательно)'
    )
    
    specialization = forms.CharField(
        max_length=200,
        required=False,
        label='Специализация',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Например: Математическая физика, Филология...'
        }),
        help_text='Ваша академическая специализация (желательно)'
    )
    
    experience_years = forms.IntegerField(
        min_value=0,
        max_value=50,
        required=True,
        label='Опыт преподавания (лет)',
        widget=forms.NumberInput(attrs={
            'class': 'form-input',
            'placeholder': '0',
            'min': '0',
            'max': '50'
        }),
        help_text='Сколько лет вы преподаете? Укажите 0, если только начинаете'
    )
    
    bio = forms.CharField(
        required=True,
        label='О себе',
        widget=forms.Textarea(attrs={
            'class': 'form-textarea',
            'placeholder': 'Расскажите о себе как о преподавателе:\n• Ваш подход к обучению\n• Что вам нравится в преподавании\n• Чего достигли ваши ученики\n• Ваши профессиональные интересы',
            'rows': 6,
            'maxlength': '1000'
        }),
        help_text='От 40 до 1000 символов. Это первое, что увидят ученики.',
        min_length=40,
        max_length=1000
    )

    def clean_bio(self):
        """✅ Валидация bio с проверкой длины"""
        bio = self.cleaned_data.get('bio')
        if bio:
            try:
                bio = bio.strip()
                bio_len = len(bio)

                if bio_len < 40:
                    raise ValidationError(
                        f'Описание слишком короткое. Минимум 40 символов (сейчас: {bio_len})'
                    )
                if bio_len > 1000:
                    raise ValidationError(
                        f'Описание слишком длинное. Максимум 1000 символов (сейчас: {bio_len})'
                    )
            except ValidationError:
                raise
            except Exception as e:
                logger.error(f"Bio validation error: {e}")
                raise ValidationError('Ошибка при проверке описания')
        return bio


class Step4AvailabilityFormatForm(forms.Form):
    """
    STEP 3: Availability & Format
    Fields: Telegram, Location, Teaching format, Working hours
    """
    # ✅ Словарь дней недели для избежания дублирования
    DAYS_OF_WEEK = {
        'monday': 'Понедельник',
        'tuesday': 'Вторник',
        'wednesday': 'Среда',
        'thursday': 'Четверг',
        'friday': 'Пятница',
        'saturday': 'Суббота',
        'sunday': 'Воскресенье'
    }
    
    telegram = forms.CharField(
        max_length=200,
        required=True,
        label='Telegram',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '@username, t.me/username или ссылка',
        }),
        help_text='@username, ссылка t.me/username или номер телефона. Ученики свяжутся с вами через Telegram.',
    )
    
    city = forms.ModelChoiceField(
        queryset=City.objects.filter(is_active=True),
        required=False,
        label='Город',
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        empty_label='Не указан / Онлайн',
        help_text='Выберите город, если преподаете офлайн'
    )
    
    teaching_format = forms.ChoiceField(
        choices=TeacherProfile.TEACHING_FORMATS,
        required=True,
        label='Формат обучения',
        widget=forms.RadioSelect(attrs={
            'class': 'format-radio'
        }),
        help_text='Как вы проводите занятия?'
    )
    
    whatsapp = forms.CharField(
        max_length=20,
        required=False,
        label='WhatsApp (желательно)',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '+998 90 123 45 67'
        }),
        help_text='Дополнительный способ связи'
    )
    
    # Расписание хранится в одном скрытом JSON-поле, которым управляет JS на шаге.
    # Формат: {"monday": [["09:00","12:00"], ["15:00","18:00"]], "tuesday": [...], ...}
    # Это позволяет задавать НЕСКОЛЬКО интервалов в день.
    schedule_data = forms.CharField(required=False, widget=forms.HiddenInput())

    # Скрытые поля для обратной совместимости с моделью (заполняются в clean()).
    available_weekdays = forms.CharField(required=False, widget=forms.HiddenInput())
    available_from = forms.TimeField(required=False, widget=forms.HiddenInput())
    available_to = forms.TimeField(required=False, widget=forms.HiddenInput())

    # Префиксы Telegram-ссылок, которые часто копипастят (без учёта регистра).
    TG_URL_PREFIXES = (
        'https://t.me/', 'http://t.me/', 't.me/',
        'https://telegram.me/', 'http://telegram.me/', 'telegram.me/',
        'https://web.telegram.org/', 'http://web.telegram.org/', 'web.telegram.org/',
        'https://', 'http://',  # на случай других вариантов
    )
    TG_USERNAME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]{3,30}[a-zA-Z0-9]$')

    def clean_telegram(self):
        """Нормализация Telegram.

        Принимаем любую разумную форму ввода:
            @username, username, t.me/username, https://t.me/username, +998901234567
        На выходе либо '@username', либо '+998…' (E.164).

        Telegram username по правилам Telegram: 5–32 символа, латинские буквы,
        цифры, '_', начинается с буквы, не заканчивается на '_'.
        Мы валидируем 5–32 и не разрешаем '_' на концах (regex выше).
        """
        raw = (self.cleaned_data.get('telegram') or '').strip()
        if not raw:
            return raw

        # 1) Срезаем URL-префиксы (если копипастили ссылку)
        low = raw.lower()
        for prefix in self.TG_URL_PREFIXES:
            if low.startswith(prefix):
                raw = raw[len(prefix):]
                break
        raw = raw.lstrip('/')

        # 2) Это телефон?
        if raw.startswith('+'):
            phone = re.sub(r'[\s\-()]', '', raw)
            if not re.match(r'^\+\d{8,15}$', phone):
                raise ValidationError(
                    'Некорректный номер телефона в Telegram. Формат: +<код страны><номер>.'
                )
            return phone

        # 3) Это username (с @ или без)
        username = raw[1:] if raw.startswith('@') else raw
        # username может содержать ? или другие хвосты от ссылки — отрезаем
        username = username.split('?')[0].split('#')[0].split('/')[0]

        if not self.TG_USERNAME_RE.match(username):
            raise ValidationError(
                'Некорректный Telegram username. Используйте 5–32 латинских букв, '
                'цифр или «_», начиная с буквы (например, @ivan_teacher).'
            )
        return '@' + username
    
    DAY_NUMBER = {
        'monday': '1', 'tuesday': '2', 'wednesday': '3', 'thursday': '4',
        'friday': '5', 'saturday': '6', 'sunday': '7',
    }

    @staticmethod
    def _parse_hhmm(value):
        """Парсит 'HH:MM' → (часы, минуты в сутках). Возвращает int минут или None."""
        if not isinstance(value, str):
            return None
        m = re.match(r'^([01]?\d|2[0-3]):([0-5]\d)$', value.strip())
        if not m:
            return None
        return int(m.group(1)) * 60 + int(m.group(2))

    def clean(self):
        """Валидация расписания (мультиинтервалы) из JSON-поля schedule_data."""
        try:
            cleaned_data = super().clean()
            import json

            raw = (cleaned_data.get('schedule_data') or '').strip()
            try:
                parsed = json.loads(raw) if raw else {}
            except (ValueError, TypeError):
                raise ValidationError('Не удалось прочитать расписание. Попробуйте ещё раз.')

            if not isinstance(parsed, dict):
                raise ValidationError('Некорректный формат расписания.')

            weekly_schedule = {}
            enabled_days = []

            for day, day_name_ru in self.DAYS_OF_WEEK.items():
                intervals = parsed.get(day) or []
                if not isinstance(intervals, list) or not intervals:
                    continue

                normalized = []  # [(start_min, end_min, from_str, to_str), ...]
                for itv in intervals:
                    # Поддерживаем форматы ["09:00","12:00"] и {"from":..,"to":..}
                    if isinstance(itv, dict):
                        f_str, t_str = itv.get('from'), itv.get('to')
                    elif isinstance(itv, (list, tuple)) and len(itv) == 2:
                        f_str, t_str = itv[0], itv[1]
                    else:
                        raise ValidationError(f'{day_name_ru}: некорректный интервал.')

                    f_min = self._parse_hhmm(f_str)
                    t_min = self._parse_hhmm(t_str)
                    if f_min is None or t_min is None:
                        raise ValidationError(f'{day_name_ru}: укажите корректное время (ЧЧ:ММ).')
                    if f_min >= t_min:
                        raise ValidationError(
                            f'{day_name_ru}: время начала должно быть раньше окончания ({f_str}–{t_str}).'
                        )
                    normalized.append((f_min, t_min, f_str.strip(), t_str.strip()))

                # Проверка пересечений интервалов внутри дня
                normalized.sort(key=lambda x: x[0])
                for prev, curr in zip(normalized, normalized[1:]):
                    if curr[0] < prev[1]:
                        raise ValidationError(
                            f'{day_name_ru}: интервалы пересекаются ({prev[2]}–{prev[3]} и {curr[2]}–{curr[3]}).'
                        )

                weekly_schedule[day] = [{'from': f, 'to': t} for _s, _e, f, t in normalized]
                enabled_days.append(self.DAY_NUMBER[day])

            # Расписание необязательно: учитель может заполнить его позже
            # через календарь. Если пусто — просто сохраняем пустые структуры.
            cleaned_data['weekly_schedule'] = weekly_schedule
            cleaned_data['available_weekdays'] = enabled_days
            return cleaned_data

        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Schedule validation error: {e}", exc_info=True)
            raise ValidationError('Ошибка при проверке расписания')


class Step5SubjectsPricingForm(forms.Form):
    """
    STEP 4: Subjects & Pricing
    Dynamic form for up to 4 subjects with pricing
    """
    TRIAL_DURATION_CHOICES = [
        ('30', '30 минут'),
        ('60', '60 минут'),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Создаем поля для 4 предметов
        for i in range(1, 5):
            self.fields[f'subject_{i}'] = forms.ModelChoiceField(
                queryset=Subject.objects.filter(is_active=True).order_by('name'),
                required=False,
                label=f'Предмет {i}',
                widget=forms.Select(attrs={
                    'class': 'form-select subject-select',
                    'data-subject-num': i
                }),
                empty_label='Выберите предмет'
            )

            self.fields[f'hourly_rate_{i}'] = forms.DecimalField(
                max_digits=10,
                decimal_places=2,
                required=False,
                label='Цена за час (сум)',
                widget=forms.NumberInput(attrs={
                    'class': 'form-input',
                    'placeholder': '100000',
                    'min': '0',
                    'step': '1000',
                    'data-subject-num': i
                })
            )

            # По умолчанию пробный урок 60 минут
            self.fields[f'trial_duration_{i}'] = forms.ChoiceField(
                choices=self.TRIAL_DURATION_CHOICES,
                initial='60',
                required=False,
                label='Длительность пробного урока',
                widget=forms.RadioSelect(attrs={
                    'class': 'trial-duration-radio',
                    'data-subject-num': i,
                }),
            )

            # По умолчанию пробный бесплатный (checked)
            self.fields[f'is_free_trial_{i}'] = forms.BooleanField(
                required=False,
                initial=True,
                label='Пробный урок бесплатно',
                widget=forms.CheckboxInput(attrs={
                    'class': 'form-checkbox free-trial-checkbox',
                    'data-subject-num': i,
                })
            )

            # Цена платного пробного — нужна только если бесплатный отключён
            self.fields[f'trial_price_{i}'] = forms.DecimalField(
                max_digits=10,
                decimal_places=2,
                required=False,
                label='Цена пробного урока (сум)',
                widget=forms.NumberInput(attrs={
                    'class': 'form-input trial-price-input',
                    'placeholder': '50000',
                    'min': '0',
                    'step': '1000',
                    'data-subject-num': i,
                })
            )

            self.fields[f'description_{i}'] = forms.CharField(
                required=False,
                label='Описание (желательно)',
                widget=forms.Textarea(attrs={
                    'class': 'form-textarea',
                    'placeholder': 'Особенности преподавания этого предмета...',
                    'rows': 2,
                    'maxlength': '200',
                    'data-subject-num': i
                }),
                max_length=200
            )

    def clean(self):
        """✅ Валидация предметов и цен"""
        try:
            cleaned_data = super().clean()
            subjects_added = 0
            selected_subjects = []

            for i in range(1, 5):
                try:
                    subject = cleaned_data.get(f'subject_{i}')
                    hourly_rate = cleaned_data.get(f'hourly_rate_{i}')
                    is_free_trial = cleaned_data.get(f'is_free_trial_{i}', True)
                    trial_price = cleaned_data.get(f'trial_price_{i}')
                    trial_duration = cleaned_data.get(f'trial_duration_{i}') or '60'

                    if subject:
                        # ✅ Проверяем, что не выбран дубликат
                        if subject in selected_subjects:
                            raise ValidationError(
                                f'Предмет "{subject}" выбран несколько раз. Выберите разные предметы.'
                            )

                        # ✅ Проверяем, что указана цена за час
                        if not hourly_rate or hourly_rate <= 0:
                            raise ValidationError(f'Укажите цену за час для предмета "{subject}"')

                        # ✅ Проверяем длительность пробного
                        if trial_duration not in ('30', '60'):
                            raise ValidationError(
                                f'Для предмета "{subject}" выберите длительность пробного урока (30 или 60 минут).'
                            )

                        # ✅ Если пробный платный — нужна цена
                        if not is_free_trial:
                            if not trial_price or trial_price <= 0:
                                raise ValidationError(
                                    f'Укажите цену пробного урока для предмета "{subject}" '
                                    f'или отметьте «Пробный урок бесплатно».'
                                )
                        else:
                            # Если бесплатный — затираем введённую цену пробного, чтобы не сохранять
                            cleaned_data[f'trial_price_{i}'] = None

                        selected_subjects.append(subject)
                        subjects_added += 1
                    elif hourly_rate and hourly_rate > 0:
                        # ✅ Указана цена, но не выбран предмет
                        raise ValidationError(f'Выберите предмет для строки {i}')
                except ValidationError:
                    raise
                except Exception as e:
                    logger.error(f"Subject {i} validation error: {e}")
                    raise ValidationError(f'Ошибка при проверке предмета {i}')

            # ✅ Проверяем, что добавлен хотя бы один предмет
            if subjects_added == 0:
                raise ValidationError('Добавьте хотя бы один предмет с ценой')

            cleaned_data['subjects_count'] = subjects_added
            return cleaned_data

        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Subjects validation error: {e}", exc_info=True)
            raise ValidationError('Ошибка при проверке предметов')


class Step6CertificatesForm(forms.Form):
    """
    STEP 6: Certificates + Video (всё необязательно, до 4 сертификатов).

    Динамически создаёт поля cert_name_{i}, cert_issuer_{i}, cert_file_{i} для i=1..4
    и одно поле video_url для presigned upload.
    """
    MAX_CERTS = 4
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
    VALID_EXTS = {'.jpg', '.jpeg', '.png', '.pdf'}
    ALLOWED_MIMES = {'image/jpeg', 'image/png', 'application/pdf'}

    video_url = forms.URLField(
        max_length=500,
        required=False,
        widget=forms.HiddenInput(attrs={'id': 'id_video_url'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for i in range(1, self.MAX_CERTS + 1):
            self.fields[f'cert_name_{i}'] = forms.CharField(
                max_length=200,
                required=False,
                label='Название сертификата',
                widget=forms.TextInput(attrs={
                    'class': 'form-input',
                    'placeholder': 'Например: Сертификат IELTS, Диплом о высшем образовании',
                    'data-cert-num': i,
                }),
            )
            self.fields[f'cert_issuer_{i}'] = forms.CharField(
                max_length=200,
                required=False,
                label='Кто выдал',
                widget=forms.TextInput(attrs={
                    'class': 'form-input',
                    'placeholder': 'Например: British Council, НУУз',
                    'data-cert-num': i,
                }),
            )
            self.fields[f'cert_file_{i}'] = forms.FileField(
                required=False,
                label='Файл сертификата',
                widget=forms.FileInput(attrs={
                    'class': 'form-file-input',
                    'accept': 'image/*,application/pdf',
                    'data-cert-num': i,
                }),
            )

    def _validate_one_file(self, file, idx):
        """Размер ≤10MB, расширение и MIME из белого списка."""
        if file.size > self.MAX_FILE_SIZE:
            raise ValidationError(
                f'Сертификат {idx}: размер файла не должен превышать 10 МБ.'
            )
        ext = '.' + file.name.lower().rsplit('.', 1)[-1] if '.' in file.name else ''
        if ext not in self.VALID_EXTS:
            raise ValidationError(
                f'Сертификат {idx}: разрешены только JPG, PNG и PDF.'
            )
        mime, _ = mimetypes.guess_type(file.name)
        if mime not in self.ALLOWED_MIMES:
            logger.warning(f"Certificate upload: недопустимый MIME type - {mime}")
            raise ValidationError(f'Сертификат {idx}: недопустимый тип файла.')

    def clean(self):
        try:
            cleaned = super().clean()
            for i in range(1, self.MAX_CERTS + 1):
                name = (cleaned.get(f'cert_name_{i}') or '').strip()
                issuer = (cleaned.get(f'cert_issuer_{i}') or '').strip()
                file = cleaned.get(f'cert_file_{i}')

                # Если все три пустые — сертификат не заполнен, скипаем.
                if not name and not issuer and not file:
                    continue

                # Если есть хотя бы один — нужны все три.
                if not (name and issuer and file):
                    raise ValidationError(
                        f'Сертификат {i}: заполните название, организацию и приложите файл '
                        f'(или очистите все три поля, чтобы пропустить).'
                    )
                self._validate_one_file(file, i)
                cleaned[f'cert_name_{i}'] = name
                cleaned[f'cert_issuer_{i}'] = issuer
            return cleaned
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Certificate validation error: {e}", exc_info=True)
            raise ValidationError('Ошибка при проверке сертификатов')
