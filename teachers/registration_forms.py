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
        ('', 'Выберите пол'),
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
        required=True,
        label='Пол',
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
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
            'autocomplete': 'tel'
        }),
        help_text='Формат: +998 XX XXX XX XX'
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
        """✅ Валидация номера телефона"""
        phone = self.cleaned_data.get('phone')
        if phone:
            try:
                phone = phone.strip()
                # ✅ Удаляем пробелы и дефисы для проверки
                phone_digits = re.sub(r'[\s\-()]', '', phone)
                
                # ✅ Проверяем формат узбекского номера
                if not re.match(r'^\+998\d{9}$', phone_digits):
                    logger.warning(f"Invalid phone format: {phone}")
                    raise ValidationError('Введите корректный номер телефона в формате +998 XX XXX XX XX')
            except ValidationError:
                raise
            except Exception as e:
                logger.error(f"Phone validation error: {e}")
                raise ValidationError('Ошибка при проверке номера телефона')
        
        return phone
    
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
        help_text='Мы отправим подтверждение на этот адрес'
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
        help_text='От 100 до 1000 символов. Это первое, что увидят ученики.',
        min_length=100,
        max_length=1000
    )
    
    def clean_bio(self):
        """✅ Валидация bio с проверкой длины"""
        bio = self.cleaned_data.get('bio')
        if bio:
            try:
                bio = bio.strip()
                bio_len = len(bio)
                
                if bio_len < 100:
                    raise ValidationError(
                        f'Описание слишком короткое. Минимум 100 символов (сейчас: {bio_len})'
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
        max_length=100,
        required=True,
        label='Telegram',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '@username или +998901234567'
        }),
        help_text='Ученики свяжутся с вами через Telegram'
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
    
    # Расписание для каждого дня недели (отдельные поля)
    # Понедельник
    monday_enabled = forms.BooleanField(required=False, label='Понедельник')
    monday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    monday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Вторник
    tuesday_enabled = forms.BooleanField(required=False, label='Вторник')
    tuesday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    tuesday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Среда
    wednesday_enabled = forms.BooleanField(required=False, label='Среда')
    wednesday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    wednesday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Четверг
    thursday_enabled = forms.BooleanField(required=False, label='Четверг')
    thursday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    thursday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Пятница
    friday_enabled = forms.BooleanField(required=False, label='Пятница')
    friday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    friday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Суббота
    saturday_enabled = forms.BooleanField(required=False, label='Суббота')
    saturday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    saturday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Воскресенье
    sunday_enabled = forms.BooleanField(required=False, label='Воскресенье')
    sunday_from = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    sunday_to = forms.TimeField(required=False, widget=forms.TimeInput(attrs={'class': 'form-input', 'type': 'time'}))
    
    # Скрытое поле для хранения всех дней (для обратной совместимости с моделью)
    available_weekdays = forms.CharField(required=False, widget=forms.HiddenInput())
    available_from = forms.TimeField(required=False, widget=forms.HiddenInput())
    available_to = forms.TimeField(required=False, widget=forms.HiddenInput())
    
    def clean_telegram(self):
        """✅ Валидация Telegram с проверкой формата"""
        telegram = self.cleaned_data.get('telegram')
        if telegram:
            try:
                telegram = telegram.strip()
                # ✅ Проверяем, что это либо @username, либо номер телефона
                if not (telegram.startswith('@') or telegram.startswith('+')):
                    raise ValidationError('Введите Telegram в формате @username или +998...')
                
                # ✅ Дополнительная проверка на пустое значение после @
                if telegram.startswith('@') and len(telegram) < 2:
                    raise ValidationError('Укажите корректный Telegram username')
            except ValidationError:
                raise
            except Exception as e:
                logger.error(f"Telegram validation error: {e}")
                raise ValidationError('Ошибка при проверке Telegram')
        return telegram
    
    def clean(self):
        """✅ Валидация расписания с проверкой дней и времени"""
        try:
            cleaned_data = super().clean()
            
            # ✅ Проверяем, что выбран хотя бы один день
            days = list(self.DAYS_OF_WEEK.keys())
            has_any_day = False
            
            for day in days:
                enabled = cleaned_data.get(f'{day}_enabled')
                time_from = cleaned_data.get(f'{day}_from')
                time_to = cleaned_data.get(f'{day}_to')
                
                if enabled:
                    has_any_day = True
                    day_name_ru = self.DAYS_OF_WEEK[day]
                    
                    # ✅ Проверяем, что если день выбран, то указаны оба времени
                    if not time_from or not time_to:
                        raise ValidationError(
                            f'{day_name_ru}: укажите время начала и окончания'
                        )
                    
                    # ✅ Проверяем, что время начала раньше времени окончания
                    if time_from >= time_to:
                        raise ValidationError(
                            f'{day_name_ru}: время начала должно быть раньше времени окончания'
                        )
            
            if not has_any_day:
                raise ValidationError('Выберите хотя бы один рабочий день')
            
            # ✅ Формируем данные для обратной совместимости
            enabled_days = []
            day_mapping = {
                'monday': '1',
                'tuesday': '2',
                'wednesday': '3',
                'thursday': '4',
                'friday': '5',
                'saturday': '6',
                'sunday': '7'
            }
            
            for day in days:
                if cleaned_data.get(f'{day}_enabled'):
                    enabled_days.append(day_mapping[day])
            
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
            
            self.fields[f'is_free_trial_{i}'] = forms.BooleanField(
                required=False,
                label='Пробный урок бесплатно',
                widget=forms.CheckboxInput(attrs={
                    'class': 'form-checkbox',
                    'data-subject-num': i
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
                    
                    if subject:
                        # ✅ Проверяем, что не выбран дубликат
                        if subject in selected_subjects:
                            raise ValidationError(
                                f'Предмет "{subject}" выбран несколько раз. Выберите разные предметы.'
                            )
                        
                        # ✅ Проверяем, что указана цена
                        if not hourly_rate or hourly_rate <= 0:
                            raise ValidationError(f'Укажите цену для предмета "{subject}"')
                        
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


class Step6CertificatesForm(forms.ModelForm):
    """
    STEP 5: Certificates (Optional)
    Upload certificates/diplomas
    """
    name = forms.CharField(
        max_length=200,
        required=False,
        label='Название сертификата',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Например: Сертификат IELTS, Диплом о высшем образовании'
        }),
        help_text='Что это за сертификат? (желательно)'
    )
    
    issuer = forms.CharField(
        max_length=200,
        required=False,
        label='Кто выдал',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Например: British Council, НУУз'
        }),
        help_text='Организация или учреждение (желательно)'
    )
    
    file = forms.FileField(
        required=False,
        label='Файл сертификата',
        widget=forms.FileInput(attrs={
            'class': 'form-file-input',
            'accept': 'image/*,application/pdf'
        }),
        help_text='PDF, JPG или PNG. Максимум 10 МБ (желательно)'
    )
    
    class Meta:
        model = Certificate
        fields = ['name', 'issuer', 'file']
    
    def clean(self):
        """✅ Валидация сертификата с проверкой файла"""
        try:
            cleaned_data = super().clean()
            name = cleaned_data.get('name')
            issuer = cleaned_data.get('issuer')
            file = cleaned_data.get('file')
            
            # ✅ Очистка данных
            if name:
                name = name.strip()
                cleaned_data['name'] = name
            if issuer:
                issuer = issuer.strip()
                cleaned_data['issuer'] = issuer
            
            # ✅ Если загружен файл, то name и issuer тоже обязательны
            if file:
                if not name or not issuer:
                    raise ValidationError('Для загруженного файла укажите название и издателя')
            
            # ✅ Если указаны name и issuer, то файл обязателен
            if (name or issuer) and not file:
                raise ValidationError('Загрузите файл сертификата')
            
            return cleaned_data
            
        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Certificate validation error: {e}", exc_info=True)
            raise ValidationError('Ошибка при проверке сертификата')
    
    def clean_file(self):
        """✅ Валидация файла сертификата с проверкой типа"""
        file = self.cleaned_data.get('file')
        if file:
            try:
                # ✅ Проверка размера файла (10 МБ максимум)
                if file.size > 10 * 1024 * 1024:
                    raise ValidationError('Размер файла не должен превышать 10 МБ')
                
                # ✅ Проверка расширения файла
                valid_extensions = ['.jpg', '.jpeg', '.png', '.pdf']
                ext = file.name.lower().split('.')[-1]
                if f'.{ext}' not in valid_extensions:
                    raise ValidationError('Разрешены только JPG, PNG и PDF форматы')
                
                # ✅ Проверка MIME type для безопасности
                mime_type, _ = mimetypes.guess_type(file.name)
                allowed_mimes = ['image/jpeg', 'image/png', 'application/pdf']
                if mime_type not in allowed_mimes:
                    logger.warning(f"Certificate upload: недопустимый MIME type - {mime_type}")
                    raise ValidationError('Недопустимый тип файла')
            
            except ValidationError:
                raise
            except Exception as e:
                logger.error(f"Certificate file validation error: {e}", exc_info=True)
                raise ValidationError('Ошибка при проверке файла сертификата')
        
        return file


class Step7VideoForm(forms.Form):
    """
    STEP 7: Video Business Card (Optional)
    Video is uploaded directly to S3/R2 via presigned URL from the frontend.
    This form only captures the resulting public URL.
    """
    video_url = forms.URLField(
        max_length=500,
        required=False,
        widget=forms.HiddenInput(attrs={'id': 'id_video_url'}),
    )
