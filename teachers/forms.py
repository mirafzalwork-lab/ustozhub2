from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from .models import User, TeacherProfile, Subject, City, Certificate, TeacherSubject

from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from .models import User, StudentProfile, Subject
class TeacherRegistrationForm(UserCreationForm):
    """Форма регистрации пользователя как учителя - ИСПРАВЛЕННАЯ"""
    
    # ========== ЛИЧНАЯ ИНФОРМАЦИЯ ==========
    first_name = forms.CharField(
        max_length=150,
        required=True,
        label='Имя',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Введите ваше имя'
        })
    )
    
    last_name = forms.CharField(
        max_length=150,
        required=True,
        label='Фамилия',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Введите вашу фамилию'
        })
    )
    
    email = forms.EmailField(
        required=True,  # ✅ ИЗМЕНЕНО: сделал обязательным
        label='Email',
        widget=forms.EmailInput(attrs={
            'class': 'form-input',
            'placeholder': 'example@mail.com'
        })
    )
    
    phone = forms.CharField(
        max_length=20,
        required=True,  # ✅ ИЗМЕНЕНО: сделал обязательным
        label='Телефон',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '+998 90 123 45 67'
        })
    )
    
    age = forms.IntegerField(
        min_value=18,
        max_value=100,
        required=True,  # ✅ ИЗМЕНЕНО: сделал обязательным
        label='Возраст',
        widget=forms.NumberInput(attrs={
            'class': 'form-input',
            'placeholder': '25'
        })
    )
    
    avatar = forms.ImageField(
        required=False,
        label='Фото профиля',
        widget=forms.FileInput(attrs={
            'class': 'form-file',
            'accept': 'image/*'
        }),
        help_text='Рекомендуемый размер: 300x300px'
    )
    
    # ========== ПРОФЕССИОНАЛЬНАЯ ИНФОРМАЦИЯ ==========
    bio = forms.CharField(
        required=False,  # ✅ ИЗМЕНЕНО: сделал обязательным
        label='О себе',
        widget=forms.Textarea(attrs={
            'class': 'form-textarea',
            'placeholder': 'Расскажите о своем опыте, методике преподавания, достижениях...',
            'rows': 5
        }),
        help_text='Минимум 100 символов',
        min_length=100,
        max_length=1000
    )
    
    education_level = forms.ChoiceField(
        choices=TeacherProfile.EDUCATION_LEVELS,
        required=False,  # ✅ ИЗМЕНЕНО: сделал обязательным
        label='Уровень образования',
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    university = forms.CharField(
        max_length=200,
        required=False,  # ✅ ИЗМЕНЕНО: сделал обязательным
        label='Учебное заведение',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Название университета/института'
        })
    )
    
    specialization = forms.CharField(
        max_length=200,
        required=True,  # ✅ ИЗМЕНЕНО: сделал обязательным
        label='Специализация',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Ваша специальность'
        })
    )
    
    experience_years = forms.IntegerField(
        min_value=0,
        max_value=50,
        required=True,  # ✅ ИЗМЕНЕНО: сделал обязательным
        label='Опыт преподавания (лет)',
        widget=forms.NumberInput(attrs={
            'class': 'form-input',
            'placeholder': '5'
        })
    )
    
    teaching_languages = forms.MultipleChoiceField(
        choices=TeacherProfile.TEACHING_LANGUAGES,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'language-checkbox'
        }),
        label='Языки преподавания',
        help_text='Выберите один или несколько языков'
    )
    
    # ========== МЕСТОПОЛОЖЕНИЕ И ФОРМАТ ==========
    city = forms.ModelChoiceField(
        queryset=City.objects.filter(is_active=True),
        required=False,
        label='Город',
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        empty_label='Выберите город'
    )
    
    teaching_format = forms.ChoiceField(
        choices=TeacherProfile.TEACHING_FORMATS,
        required=True,
        label='Формат обучения',
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    # ========== КОНТАКТЫ ==========
    telegram = forms.CharField(
        max_length=100,
        required=False,
        label='Telegram',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '@username или номер телефона'
        })
    )
    
    whatsapp = forms.CharField(
        max_length=20,
        required=False,
        label='WhatsApp',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '+998 90 123 45 67'
        })
    )
    
    # ========== ВРЕМЯ РАБОТЫ ==========
    available_from = forms.TimeField(
        required=True,
        label='Доступен с',
        widget=forms.TimeInput(attrs={
            'class': 'form-input',
            'type': 'time'
        }),
        initial='09:00'
    )
    
    available_to = forms.TimeField(
        required=True,
        label='Доступен до',
        widget=forms.TimeInput(attrs={
            'class': 'form-input',
            'type': 'time'
        }),
        initial='21:00'
    )
    
    available_weekdays = forms.MultipleChoiceField(
        choices=[
            ('1', 'Понедельник'),
            ('2', 'Вторник'),
            ('3', 'Среда'),
            ('4', 'Четверг'),
            ('5', 'Пятница'),
            ('6', 'Суббота'),
            ('7', 'Воскресенье'),
        ],
        required=True,
        label='Рабочие дни',
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'form-checkbox'
        })
    )
    
    # ========== СОГЛАСИЕ ==========
    terms_accepted = forms.BooleanField(
        required=True,
        label='Я принимаю условия использования и политику конфиденциальности',
        widget=forms.CheckboxInput(attrs={
            'class': 'form-checkbox'
        })
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2', 'first_name', 
                  'last_name', 'phone', 'age', 'avatar']
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': 'Имя пользователя'
        })
        self.fields['password1'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': 'Введите пароль'
        })
        self.fields['password2'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': 'Повторите пароль'
        })
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and User.objects.filter(email=email).exists():
            raise ValidationError('Этот email уже используется')
        return email
    
    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if phone and User.objects.filter(phone=phone).exists():
            raise ValidationError('Этот номер телефона уже используется')
        return phone
    
    def clean_available_weekdays(self):
        days = self.cleaned_data.get('available_weekdays')
        if not days:
            raise ValidationError('Выберите хотя бы один рабочий день')
        return ','.join(days)
    
    def clean_teaching_languages(self):
        """✅ ДОБАВЛЕНО: валидация языков"""
        languages = self.cleaned_data.get('teaching_languages')
        if not languages:
            raise ValidationError('Выберите хотя бы один язык преподавания')
        return languages
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.user_type = 'teacher'
        user.email = self.cleaned_data['email']
        user.phone = self.cleaned_data['phone']
        user.age = self.cleaned_data['age']
        
        if commit:
            user.save()
            
            # ✅ ИСПРАВЛЕНО: Преобразуем список в строку ДО создания профиля
            languages_str = ','.join(self.cleaned_data['teaching_languages'])

            # Создаем профиль учителя со статусом "на модерации"
            teacher_profile = TeacherProfile.objects.create(
                user=user,
                bio=self.cleaned_data['bio'],
                education_level=self.cleaned_data['education_level'],
                university=self.cleaned_data['university'],
                specialization=self.cleaned_data['specialization'],
                experience_years=self.cleaned_data['experience_years'],
                city=self.cleaned_data.get('city'),
                teaching_format=self.cleaned_data['teaching_format'],
                telegram=self.cleaned_data.get('telegram', ''),
                whatsapp=self.cleaned_data.get('whatsapp', ''),
                available_from=self.cleaned_data['available_from'],
                available_to=self.cleaned_data['available_to'],
                teaching_languages=languages_str,  # ✅ ИСПРАВЛЕНО: используем строку
                available_weekdays=self.cleaned_data['available_weekdays'],  # Уже строка из clean_available_weekdays
                moderation_status='pending',  # ✅ ДОБАВЛЕНО: явно устанавливаем статус
                is_active=False  # Неактивен до модерации
            )
            
        return user


class TeacherSubjectsForm(forms.Form):
    """Форма для добавления предметов и цен (второй шаг регистрации)"""
    
    def __init__(self, *args, **kwargs):
        teacher = kwargs.pop('teacher', None)
        super().__init__(*args, **kwargs)
        
        # Динамически создаем поля для предметов
        subjects = Subject.objects.filter(is_active=True)
        
        for i in range(1, 6):  # До 5 предметов
            self.fields[f'subject_{i}'] = forms.ModelChoiceField(
                queryset=subjects,
                required=False,
                label=f'Предмет {i}',
                widget=forms.Select(attrs={
                    'class': 'form-select',
                    'onchange': f'togglePriceField({i})'
                }),
                empty_label='Выберите предмет'
            )
            
            self.fields[f'hourly_rate_{i}'] = forms.DecimalField(
                max_digits=10,
                decimal_places=2,
                required=False,
                label=f'Цена за час (сум)',
                min_value=0,
                widget=forms.NumberInput(attrs={
                    'class': 'form-input',
                    'placeholder': '50000',
                    'id': f'price_{i}'
                })
            )
            
            self.fields[f'is_free_trial_{i}'] = forms.BooleanField(
                required=False,
                label='Бесплатное пробное занятие',
                widget=forms.CheckboxInput(attrs={
                    'class': 'form-checkbox'
                })
            )
            
            self.fields[f'description_{i}'] = forms.CharField(
                required=False,
                label='Описание',
                widget=forms.Textarea(attrs={
                    'class': 'form-textarea',
                    'rows': 2,
                    'placeholder': 'Дополнительная информация о преподавании этого предмета'
                }),
                max_length=500
            )
    
    def clean(self):
        cleaned_data = super().clean()
        
        # Проверяем, что добавлен хотя бы один предмет
        has_subject = False
        for i in range(1, 6):
            subject = cleaned_data.get(f'subject_{i}')
            hourly_rate = cleaned_data.get(f'hourly_rate_{i}')
            
            if subject:
                has_subject = True
                if not hourly_rate or hourly_rate <= 0:
                    raise ValidationError(f'Укажите цену для предмета {i}')
        
        if not has_subject:
            raise ValidationError('Добавьте хотя бы один предмет')
        
        return cleaned_data


class CertificateUploadForm(forms.ModelForm):
    """Форма для загрузки сертификатов (опциональный третий шаг)"""
    
    class Meta:
        model = Certificate
        fields = ['name', 'issuer', 'file']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Название сертификата'
            }),
            'issuer': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Кто выдал'
            }),
            'file': forms.FileInput(attrs={
                'class': 'form-file',
                'accept': '.pdf,.jpg,.jpeg,.png'
            })
        }
        labels = {
            'name': 'Название сертификата',
            'issuer': 'Организация/учреждение',
            'file': 'Файл сертификата'
        }

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.core.exceptions import ValidationError
from .models import User, StudentProfile

class LoginForm(AuthenticationForm):
    """Форма входа"""
    username = forms.CharField(
        label='Имя пользователя или Email',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Введите имя пользователя или email',
            'autofocus': True
        })
    )
    
    password = forms.CharField(
        label='Пароль',
        widget=forms.PasswordInput(attrs={
            'class': 'form-input',
            'placeholder': 'Введите пароль'
        })
    )
    
    remember_me = forms.BooleanField(
        required=False,
        initial=True,
        label='Запомнить меня',
        widget=forms.CheckboxInput(attrs={
            'class': 'form-checkbox'
        })
    )

# Добавьте этот код в ваш forms.py, заменив класс StudentRegistrationForm

class StudentRegistrationForm(UserCreationForm):
    """Форма регистрации ученика"""
    
    # Личная информация
    first_name = forms.CharField(
        max_length=150,
        required=True,
        label='Имя',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Введите ваше имя'
        })
    )
    
    last_name = forms.CharField(
        max_length=150,
        required=True,
        label='Фамилия',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Введите вашу фамилию'
        })
    )
    
    email = forms.EmailField(
        required=False,
        label='Email',
        widget=forms.EmailInput(attrs={
            'class': 'form-input',
            'placeholder': 'example@mail.com'
        })
    )
    
    phone = forms.CharField(
        max_length=20,
        required=True,
        label='Телефон',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': '+998 90 123 45 67'
        })
    )
    
    age = forms.IntegerField(
        min_value=10,
        max_value=100,
        required=False,
        label='Возраст',
        widget=forms.NumberInput(attrs={
            'class': 'form-input',
            'placeholder': '18'
        })
    )
    
    # ➕ НОВОЕ: Город
    city = forms.ModelChoiceField(
        queryset=City.objects.filter(is_active=True),
        required=False,
        label='Город',
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        empty_label='Выберите город'
    )
    
    # Предметы для изучения
    interests = forms.ModelMultipleChoiceField(
        queryset=Subject.objects.filter(is_active=True),
        required=True,
        label='Предметы, которые хочу изучать',
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'subject-checkbox'
        }),
        help_text='Выберите один или несколько предметов'
    )
    
    # Описание целей обучения
    bio = forms.CharField(
        required=False,
        label='Расскажите о ваших целях',
        widget=forms.Textarea(attrs={
            'class': 'form-textarea',
            'placeholder': 'Например: Хочу подготовиться к экзаменам, улучшить знания по математике, изучить английский с нуля...',
            'rows': 4
        }),
        help_text='Минимум 20 символов - это поможет учителям лучше понять ваши потребности',
        min_length=20,
        max_length=1000
    )
    
    # Уровень образования
    education_level = forms.ChoiceField(
        choices=StudentProfile.EDUCATION_LEVELS,
        required=False,
        label='Уровень образования',
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    # Школа/Университет
    school_university = forms.CharField(
        max_length=200,
        required=False,
        label='Школа/Университет',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Название вашей школы или университета'
        })
    )
    
    # ➕ НОВОЕ: Формат обучения
    learning_format = forms.ChoiceField(
        choices=StudentProfile.LEARNING_FORMATS,
        required=True,
        label='Предпочитаемый формат обучения',
        widget=forms.Select(attrs={
            'class': 'form-select'
        }),
        initial='both'
    )
    
    # ➕ НОВОЕ: Бюджет минимальный
    budget_min = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        label='Минимальный бюджет (сум/час)',
        min_value=0,
        widget=forms.NumberInput(attrs={
            'class': 'form-input',
            'placeholder': '30000',
            'step': '1000'
        }),
        help_text='Минимальная цена, которую готовы платить'
    )
    
    # ➕ НОВОЕ: Бюджет максимальный
    budget_max = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        label='Максимальный бюджет (сум/час)',
        min_value=0,
        widget=forms.NumberInput(attrs={
            'class': 'form-input',
            'placeholder': '100000',
            'step': '1000'
        }),
        help_text='Максимальная цена, которую готовы платить'
    )
    
    # ➕ НОВОЕ: Доступные дни недели
    available_weekdays = forms.MultipleChoiceField(
        choices=[
            ('1', 'Понедельник'),
            ('2', 'Вторник'),
            ('3', 'Среда'),
            ('4', 'Четверг'),
            ('5', 'Пятница'),
            ('6', 'Суббота'),
            ('7', 'Воскресенье'),
        ],
        required=False,
        label='Доступные дни для занятий',
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'weekday-checkbox'
        }),
        help_text='Выберите удобные дни для занятий'
    )
    
    # Согласие
    terms_accepted = forms.BooleanField(
        required=True,
        label='Я принимаю условия использования и политику конфиденциальности',
        widget=forms.CheckboxInput(attrs={
            'class': 'form-checkbox'
        })
    )

    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2', 'first_name', 
                  'last_name', 'phone', 'age']
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': 'Имя пользователя'
        })
        self.fields['password1'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': 'Введите пароль'
        })
        self.fields['password2'].widget.attrs.update({
            'class': 'form-input',
            'placeholder': 'Повторите пароль'
        })
        
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and User.objects.filter(email=email).exists():
            raise ValidationError('Этот email уже используется')
        return email
    
    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if phone and User.objects.filter(phone=phone).exists():
            raise ValidationError('Этот номер телефона уже используется')
        return phone
    
    def clean_interests(self):
        interests = self.cleaned_data.get('interests')
        if not interests:
            raise ValidationError('Выберите хотя бы один предмет')
        if interests.count() > 10:
            raise ValidationError('Можно выбрать максимум 10 предметов')
        return interests
    
    def clean(self):
        cleaned_data = super().clean()
        budget_min = cleaned_data.get('budget_min')
        budget_max = cleaned_data.get('budget_max')
        
        # Проверка, что максимальный бюджет больше минимального
        if budget_min and budget_max and budget_min > budget_max:
            raise ValidationError('Максимальный бюджет должен быть больше минимального')
        
        return cleaned_data
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.user_type = 'student'
        user.email = self.cleaned_data['email']
        user.phone = self.cleaned_data['phone']
        user.age = self.cleaned_data.get('age')
        
        if commit:
            user.save()
            
            # Создаем профиль ученика с ВСЕМИ полями
            student_profile = StudentProfile.objects.create(
                user=user,
                bio=self.cleaned_data.get('bio', ''),
                description=self.cleaned_data.get('bio', ''),  # Используем bio как description
                education_level=self.cleaned_data.get('education_level', ''),
                school_university=self.cleaned_data.get('school_university', ''),
                city=self.cleaned_data.get('city'),
                learning_format=self.cleaned_data.get('learning_format', 'both'),
                budget_min=self.cleaned_data.get('budget_min'),
                budget_max=self.cleaned_data.get('budget_max'),
                available_weekdays=','.join(self.cleaned_data.get('available_weekdays', [])) if self.cleaned_data.get('available_weekdays') else '1,2,3,4,5,6,7',
                is_active=True
            )
            
            # Связываем выбранные предметы
            interests = self.cleaned_data['interests']
            student_profile.interests.set(interests)
            student_profile.desired_subjects.set(interests)  # Также сохраняем как желаемые предметы
            
        return user

# Добавьте эти классы в ваш forms.py

from django import forms
from .models import TeacherProfile, StudentProfile, City, Subject

class TeacherProfileEditForm(forms.ModelForm):
    """Форма редактирования профиля учителя"""
    
    teaching_languages = forms.MultipleChoiceField(
        choices=TeacherProfile.TEACHING_LANGUAGES,
        required=True,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'language-checkbox'
        }),
        label='Языки преподавания'
    )
    
    available_weekdays = forms.MultipleChoiceField(
        choices=[
            ('1', 'Понедельник'),
            ('2', 'Вторник'),
            ('3', 'Среда'),
            ('4', 'Четверг'),
            ('5', 'Пятница'),
            ('6', 'Суббота'),
            ('7', 'Воскресенье'),
        ],
        required=True,
        label='Рабочие дни',
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'weekday-checkbox'
        })
    )
    
    class Meta:
        model = TeacherProfile
        fields = [
            'bio', 'education_level', 'university', 'specialization',
            'experience_years', 'city', 'teaching_format', 'telegram',
            'whatsapp', 'available_from', 'available_to', 'is_active'
        ]
        widgets = {
            'bio': forms.Textarea(attrs={
                'class': 'form-textarea',
                'rows': 5,
                'placeholder': 'Расскажите о своем опыте преподавания...'
            }),
            'education_level': forms.Select(attrs={'class': 'form-select'}),
            'university': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Название университета'
            }),
            'specialization': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ваша специальность'
            }),
            'experience_years': forms.NumberInput(attrs={
                'class': 'form-input',
                'min': 0,
                'max': 50
            }),
            'city': forms.Select(attrs={'class': 'form-select'}),
            'teaching_format': forms.Select(attrs={'class': 'form-select'}),
            'telegram': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': '@username'
            }),
            'whatsapp': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': '+998 90 123 45 67'
            }),
            'available_from': forms.TimeInput(attrs={
                'class': 'form-input',
                'type': 'time'
            }),
            'available_to': forms.TimeInput(attrs={
                'class': 'form-input',
                'type': 'time'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-checkbox-large'
            })
        }
        labels = {
            'bio': 'О себе',
            'education_level': 'Уровень образования',
            'university': 'Учебное заведение',
            'specialization': 'Специализация',
            'experience_years': 'Опыт преподавания (лет)',
            'city': 'Город',
            'teaching_format': 'Формат обучения',
            'telegram': 'Telegram',
            'whatsapp': 'WhatsApp',
            'available_from': 'Доступен с',
            'available_to': 'Доступен до',
            'is_active': 'Отображать в поиске учителей'
        }
        help_texts = {
            'is_active': 'Если отключено, ваш профиль не будет показываться в поиске'
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Заполняем начальные значения для MultipleChoiceField
        if self.instance and self.instance.pk:
            if self.instance.teaching_languages:
                self.initial['teaching_languages'] = self.instance.teaching_languages.split(',')
            
            if self.instance.available_weekdays:
                self.initial['available_weekdays'] = self.instance.available_weekdays.split(',')
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        
        # Преобразуем списки в строки
        instance.teaching_languages = ','.join(self.cleaned_data['teaching_languages'])
        instance.available_weekdays = ','.join(self.cleaned_data['available_weekdays'])
        
        if commit:
            instance.save()
        
        return instance


class StudentProfileEditForm(forms.ModelForm):
    """Форма редактирования профиля ученика"""
    
    interests = forms.ModelMultipleChoiceField(
        queryset=Subject.objects.filter(is_active=True),
        required=False,
        label='Интересующие предметы',
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'subject-checkbox'
        })
    )
    
    desired_subjects = forms.ModelMultipleChoiceField(
        queryset=Subject.objects.filter(is_active=True),
        required=True,
        label='Предметы для изучения',
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'subject-checkbox'
        }),
        help_text='Выберите предметы, которые хотите изучать'
    )
    
    available_weekdays = forms.MultipleChoiceField(
        choices=[
            ('1', 'Понедельник'),
            ('2', 'Вторник'),
            ('3', 'Среда'),
            ('4', 'Четверг'),
            ('5', 'Пятница'),
            ('6', 'Суббота'),
            ('7', 'Воскресенье'),
        ],
        required=False,
        label='Доступные дни для занятий',
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'weekday-checkbox'
        })
    )
    
    class Meta:
        model = StudentProfile
        fields = [
            'bio', 'description', 'education_level', 'school_university',
            'city', 'learning_format', 'budget_min', 'budget_max', 'is_active'
        ]
        widgets = {
            'bio': forms.Textarea(attrs={
                'class': 'form-textarea',
                'rows': 3,
                'placeholder': 'Краткое описание...'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-textarea',
                'rows': 5,
                'placeholder': 'Расскажите о своих целях обучения...'
            }),
            'education_level': forms.Select(attrs={'class': 'form-select'}),
            'school_university': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Название школы/университета'
            }),
            'city': forms.Select(attrs={'class': 'form-select'}),
            'learning_format': forms.Select(attrs={'class': 'form-select'}),
            'budget_min': forms.NumberInput(attrs={
                'class': 'form-input',
                'placeholder': '30000',
                'step': '1000'
            }),
            'budget_max': forms.NumberInput(attrs={
                'class': 'form-input',
                'placeholder': '100000',
                'step': '1000'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-checkbox-large'
            })
        }
        labels = {
            'bio': 'Краткое описание',
            'description': 'Подробное описание целей',
            'education_level': 'Уровень образования',
            'school_university': 'Школа/Университет',
            'city': 'Город',
            'learning_format': 'Предпочитаемый формат',
            'budget_min': 'Минимальный бюджет (сум/час)',
            'budget_max': 'Максимальный бюджет (сум/час)',
            'is_active': 'Отображать в поиске учеников'
        }
        help_texts = {
            'is_active': 'Если отключено, ваш профиль не будет показываться в поиске',
            'budget_min': 'Минимальная цена, которую готовы платить',
            'budget_max': 'Максимальная цена, которую готовы платить'
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Заполняем начальные значения
        if self.instance and self.instance.pk:
            self.initial['interests'] = self.instance.interests.all()
            self.initial['desired_subjects'] = self.instance.desired_subjects.all()
            
            if self.instance.available_weekdays:
                self.initial['available_weekdays'] = self.instance.available_weekdays.split(',')
    
    def clean(self):
        cleaned_data = super().clean()
        budget_min = cleaned_data.get('budget_min')
        budget_max = cleaned_data.get('budget_max')
        
        if budget_min and budget_max and budget_min > budget_max:
            raise forms.ValidationError(
                'Максимальный бюджет должен быть больше минимального'
            )
        
        return cleaned_data
    
    def save(self, commit=True):
        instance = super().save(commit=False)
        
        # Преобразуем список дней в строку
        weekdays = self.cleaned_data.get('available_weekdays', [])
        instance.available_weekdays = ','.join(weekdays) if weekdays else '1,2,3,4,5,6,7'
        
        if commit:
            instance.save()
            
            # Сохраняем связи Many-to-Many
            self.save_m2m()
            
            # Обновляем предметы
            interests = self.cleaned_data.get('interests', [])
            desired_subjects = self.cleaned_data.get('desired_subjects', [])
            
            instance.interests.set(interests)
            instance.desired_subjects.set(desired_subjects)
        
        return instance


class UserProfileEditForm(forms.ModelForm):
    """Форма редактирования базовой информации пользователя"""
    
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'phone', 'age', 'avatar']
        widgets = {
            'first_name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ваше имя'
            }),
            'last_name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ваша фамилия'
            }),
            'email': forms.EmailInput(attrs={
                'class': 'form-input',
                'placeholder': 'email@example.com'
            }),
            'phone': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': '+998 90 123 45 67'
            }),
            'age': forms.NumberInput(attrs={
                'class': 'form-input',
                'min': 10,
                'max': 100
            }),
            'avatar': forms.FileInput(attrs={
                'class': 'form-file',
                'accept': 'image/*'
            })
        }
        labels = {
            'first_name': 'Имя',
            'last_name': 'Фамилия',
            'email': 'Email',
            'phone': 'Телефон',
            'age': 'Возраст',
            'avatar': 'Фото профиля'
        }
        help_texts = {
            'avatar': 'Рекомендуемый размер: 300x300px'
        }
    
    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            # Проверяем, не используется ли email другим пользователем
            existing = User.objects.filter(email=email).exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError('Этот email уже используется')
        return email
    
    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if phone:
            # Проверяем, не используется ли телефон другим пользователем
            existing = User.objects.filter(phone=phone).exclude(pk=self.instance.pk)
            if existing.exists():
                raise forms.ValidationError('Этот номер телефона уже используется')
        return phone