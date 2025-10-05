from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import ValidationError
from .models import User, TeacherProfile, Subject, City, Certificate, TeacherSubject

class TeacherRegistrationForm(UserCreationForm):
    """Форма регистрации пользователя как учителя"""
    
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
        required=True,
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
        min_value=18,
        max_value=100,
        required=True,
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
    
    # Профессиональная информация
    bio = forms.CharField(
        required=True,
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
        required=True,
        label='Уровень образования',
        widget=forms.Select(attrs={
            'class': 'form-select'
        })
    )
    
    university = forms.CharField(
        max_length=200,
        required=True,
        label='Учебное заведение',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Название университета/института'
        })
    )
    
    specialization = forms.CharField(
        max_length=200,
        required=True,
        label='Специализация',
        widget=forms.TextInput(attrs={
            'class': 'form-input',
            'placeholder': 'Ваша специальность'
        })
    )
    
    experience_years = forms.IntegerField(
        min_value=0,
        max_value=50,
        required=True,
        label='Опыт преподавания (лет)',
        widget=forms.NumberInput(attrs={
            'class': 'form-input',
            'placeholder': '5'
        })
    )
    teaching_languages = forms.MultipleChoiceField(
        choices=TeacherProfile.TEACHING_LANGUAGES,
        required=True,
        widget=forms.CheckboxSelectMultiple(attrs={
            'class': 'language-checkbox'
        }),
        label='Языки преподавания',
        help_text='Выберите один или несколько языков'
    )
    
    # Местоположение и формат
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
    
    # Контакты
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
    
    # Время работы
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
        if User.objects.filter(email=email).exists():
            raise ValidationError('Этот email уже используется')
        return email
    
    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if User.objects.filter(phone=phone).exists():
            raise ValidationError('Этот номер телефона уже используется')
        return phone
    
    def clean_available_weekdays(self):
        days = self.cleaned_data.get('available_weekdays')
        if not days:
            raise ValidationError('Выберите хотя бы один рабочий день')
        return ','.join(days)
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.user_type = 'teacher'
        user.email = self.cleaned_data['email']
        user.phone = self.cleaned_data['phone']
        user.age = self.cleaned_data['age']
        
        if commit:
            user.save()
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
                teaching_languages=languages_str,

                available_weekdays=self.cleaned_data['available_weekdays'],
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


class StudentRegistrationForm(UserCreationForm):
    """Форма регистрации ученика"""
    
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
        required=True,
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
        if User.objects.filter(email=email).exists():
            raise ValidationError('Этот email уже используется')
        return email
    
    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        if User.objects.filter(phone=phone).exists():
            raise ValidationError('Этот номер телефона уже используется')
        return phone
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.user_type = 'student'
        user.email = self.cleaned_data['email']
        user.phone = self.cleaned_data['phone']
        user.age = self.cleaned_data.get('age')
        
        if commit:
            user.save()
            # Создаем профиль ученика
            StudentProfile.objects.create(user=user)
            
        return user