/**
 * Professional Teacher Registration JavaScript
 * Client-side validation and UX enhancements
 */

document.addEventListener('DOMContentLoaded', function() {
    // Initialize all components
    initPhotoPreview();
    initPasswordStrength();
    initCharCounter();
    initFileUpload();
    initFormValidation();
});

/**
 * Photo Preview
 * Shows preview of uploaded avatar
 */
function initPhotoPreview() {
    const avatarInput = document.getElementById('id_avatar');
    const photoPreview = document.getElementById('photoPreview');
    
    if (!avatarInput || !photoPreview) return;
    
    // Click on preview to trigger file input
    photoPreview.addEventListener('click', function() {
        avatarInput.click();
    });
    
    // Handle file selection
    avatarInput.addEventListener('change', function(e) {
        const file = e.target.files[0];
        
        if (file) {
            // Validate file type
            const validTypes = ['image/jpeg', 'image/jpg', 'image/png'];
            if (!validTypes.includes(file.type)) {
                showError(avatarInput, 'Пожалуйста, выберите файл JPG, JPEG или PNG');
                return;
            }
            
            // Validate file size (5MB)
            if (file.size > 5 * 1024 * 1024) {
                showError(avatarInput, 'Размер файла не должен превышать 5 МБ');
                return;
            }
            
            // Show preview
            const reader = new FileReader();
            reader.onload = function(e) {
                photoPreview.innerHTML = `<img src="${e.target.result}" alt="Preview">`;
                photoPreview.classList.add('has-image');
            };
            reader.readAsDataURL(file);
            
            clearError(avatarInput);
        }
    });
}

/**
 * Password Strength Indicator
 * Shows password strength as user types
 */
function initPasswordStrength() {
    const passwordInput = document.getElementById('id_password1');
    const strengthFill = document.getElementById('strengthFill');
    const strengthText = document.getElementById('strengthText');
    
    if (!passwordInput || !strengthFill || !strengthText) return;
    
    passwordInput.addEventListener('input', function() {
        const password = this.value;
        const strength = calculatePasswordStrength(password);
        
        // Update visual indicator
        strengthFill.className = 'strength-fill';
        
        if (password.length === 0) {
            strengthFill.style.width = '0%';
            strengthText.textContent = 'Введите пароль';
            return;
        }
        
        if (strength < 3) {
            strengthFill.classList.add('weak');
            strengthText.textContent = 'Слабый пароль';
            strengthText.style.color = '#ef4444';
        } else if (strength < 5) {
            strengthFill.classList.add('medium');
            strengthText.textContent = 'Средний пароль';
            strengthText.style.color = '#f59e0b';
        } else {
            strengthFill.classList.add('strong');
            strengthText.textContent = 'Надежный пароль';
            strengthText.style.color = '#10b981';
        }
    });
}

function calculatePasswordStrength(password) {
    let strength = 0;
    
    if (password.length >= 8) strength++;
    if (password.length >= 12) strength++;
    if (/[a-z]/.test(password)) strength++;
    if (/[A-Z]/.test(password)) strength++;
    if (/[0-9]/.test(password)) strength++;
    if (/[^a-zA-Z0-9]/.test(password)) strength++;
    
    return strength;
}

/**
 * Character Counter
 * Shows character count for bio textarea
 */
function initCharCounter() {
    const bioTextarea = document.querySelector('textarea[name$="bio"]');
    const charCount = document.getElementById('charCount');
    
    if (!bioTextarea || !charCount) return;
    
    // Update counter on input
    bioTextarea.addEventListener('input', function() {
        const count = this.value.length;
        charCount.textContent = count;
        
        // Change color based on limits
        if (count < 100) {
            charCount.style.color = '#ef4444';
        } else if (count > 1000) {
            charCount.style.color = '#ef4444';
        } else {
            charCount.style.color = '#10b981';
        }
    });
    
    // Initial count
    charCount.textContent = bioTextarea.value.length;
}

/**
 * File Upload Enhancement
 * Better UX for certificate upload
 */
function initFileUpload() {
    const fileInput = document.querySelector('input[name$="file"]');
    const uploadArea = document.getElementById('certificateUpload');
    
    if (!fileInput || !uploadArea) return;
    
    // Click on upload area to trigger file input
    uploadArea.addEventListener('click', function() {
        fileInput.click();
    });
    
    // Drag and drop
    uploadArea.addEventListener('dragover', function(e) {
        e.preventDefault();
        this.style.borderColor = '#3b82f6';
        this.style.background = 'rgba(37, 99, 235, 0.05)';
    });
    
    uploadArea.addEventListener('dragleave', function(e) {
        e.preventDefault();
        this.style.borderColor = '';
        this.style.background = '';
    });
    
    uploadArea.addEventListener('drop', function(e) {
        e.preventDefault();
        this.style.borderColor = '';
        this.style.background = '';
        
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            fileInput.files = files;
            updateFileUploadUI(files[0]);
        }
    });
    
    // Handle file selection
    fileInput.addEventListener('change', function(e) {
        if (this.files.length > 0) {
            updateFileUploadUI(this.files[0]);
        }
    });
}

function updateFileUploadUI(file) {
    const uploadArea = document.getElementById('certificateUpload');
    if (!uploadArea) return;
    
    // Validate file
    const validTypes = ['image/jpeg', 'image/jpg', 'image/png', 'application/pdf'];
    if (!validTypes.includes(file.type)) {
        showError(uploadArea, 'Пожалуйста, выберите файл JPG, PNG или PDF');
        return;
    }
    
    if (file.size > 10 * 1024 * 1024) {
        showError(uploadArea, 'Размер файла не должен превышать 10 МБ');
        return;
    }
    
    // Update UI
    uploadArea.innerHTML = `
        <svg class="upload-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
        <p class="upload-text">${file.name}</p>
        <p class="upload-hint">Файл выбран (${formatFileSize(file.size)})</p>
    `;
    
    clearError(uploadArea);
}

/**
 * Form Validation
 * Client-side validation before submission
 */
function initFormValidation() {
    const form = document.querySelector('.registration-form');
    if (!form) return;
    
    form.addEventListener('submit', function(e) {
        let isValid = true;
        
        // Clear previous errors
        document.querySelectorAll('.field-errors').forEach(el => el.remove());
        
        // Validate required fields
        const requiredFields = form.querySelectorAll('[required]');
        requiredFields.forEach(field => {
            if (!field.value.trim()) {
                showError(field, 'Это поле обязательно для заполнения');
                isValid = false;
            }
        });
        
        // Validate email
        const emailField = form.querySelector('input[type="email"]');
        if (emailField && emailField.value) {
            if (!isValidEmail(emailField.value)) {
                showError(emailField, 'Введите корректный email адрес');
                isValid = false;
            }
        }
        
        // Validate phone
        const phoneField = form.querySelector('input[name$="phone"]');
        if (phoneField && phoneField.value) {
            const phonePattern = /^\+998\d{9}$/;
            const phoneDigits = phoneField.value.replace(/[\s\-]/g, '');
            if (!phonePattern.test(phoneDigits)) {
                showError(phoneField, 'Введите корректный номер телефона в формате +998 XX XXX XX XX');
                isValid = false;
            }
        }
        
        // Validate password match
        const password1 = form.querySelector('input[name$="password1"]');
        const password2 = form.querySelector('input[name$="password2"]');
        if (password1 && password2) {
            if (password1.value !== password2.value) {
                showError(password2, 'Пароли не совпадают');
                isValid = false;
            }
        }
        
        // Validate bio length
        const bioField = form.querySelector('textarea[name$="bio"]');
        if (bioField && bioField.value) {
            const length = bioField.value.trim().length;
            if (length < 100) {
                showError(bioField, `Описание слишком короткое. Минимум 100 символов (сейчас: ${length})`);
                isValid = false;
            } else if (length > 1000) {
                showError(bioField, `Описание слишком длинное. Максимум 1000 символов (сейчас: ${length})`);
                isValid = false;
            }
        }
        
        // Validate time range
        const timeFrom = form.querySelector('input[name$="available_from"]');
        const timeTo = form.querySelector('input[name$="available_to"]');
        if (timeFrom && timeTo && timeFrom.value && timeTo.value) {
            if (timeFrom.value >= timeTo.value) {
                showError(timeTo, 'Время окончания должно быть позже времени начала');
                isValid = false;
            }
        }
        
        // Validate at least one teaching language
        const languageCheckboxes = form.querySelectorAll('input[name$="teaching_languages"]');
        if (languageCheckboxes.length > 0) {
            const isChecked = Array.from(languageCheckboxes).some(cb => cb.checked);
            if (!isChecked) {
                const container = languageCheckboxes[0].closest('.form-group');
                showError(container, 'Выберите хотя бы один язык преподавания');
                isValid = false;
            }
        }
        
        // Validate at least one weekday
        const weekdayCheckboxes = form.querySelectorAll('input[name$="available_weekdays"]');
        if (weekdayCheckboxes.length > 0) {
            const isChecked = Array.from(weekdayCheckboxes).some(cb => cb.checked);
            if (!isChecked) {
                const container = weekdayCheckboxes[0].closest('.form-group');
                showError(container, 'Выберите хотя бы один рабочий день');
                isValid = false;
            }
        }
        
        // Validate subjects and prices
        const subject1 = form.querySelector('select[name$="subject_1"]');
        const price1 = form.querySelector('input[name$="hourly_rate_1"]');
        if (subject1 && price1) {
            if (!subject1.value || !price1.value || parseFloat(price1.value) <= 0) {
                showError(subject1, 'Добавьте хотя бы один предмет с ценой');
                isValid = false;
            }
        }
        
        // Scroll to first error
        if (!isValid) {
            e.preventDefault();
            const firstError = document.querySelector('.field-errors');
            if (firstError) {
                firstError.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }
    });
}

/**
 * Helper Functions
 */

function showError(field, message) {
    // Remove existing error
    clearError(field);
    
    // Add error message
    const errorDiv = document.createElement('div');
    errorDiv.className = 'field-errors';
    errorDiv.innerHTML = `<p class="error-text">${message}</p>`;
    
    // Insert after field or its container
    if (field.classList.contains('form-group')) {
        field.appendChild(errorDiv);
    } else {
        field.parentNode.insertBefore(errorDiv, field.nextSibling);
    }
    
    // Add error styling to field
    if (field.tagName === 'INPUT' || field.tagName === 'TEXTAREA' || field.tagName === 'SELECT') {
        field.style.borderColor = '#ef4444';
    }
}

function clearError(field) {
    // Remove error message
    const errorDiv = field.nextElementSibling;
    if (errorDiv && errorDiv.classList.contains('field-errors')) {
        errorDiv.remove();
    }
    
    // Also check parent for errors
    const parentError = field.parentNode.querySelector('.field-errors');
    if (parentError) {
        parentError.remove();
    }
    
    // Remove error styling
    if (field.tagName === 'INPUT' || field.tagName === 'TEXTAREA' || field.tagName === 'SELECT') {
        field.style.borderColor = '';
    }
}

function isValidEmail(email) {
    const pattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return pattern.test(email);
}

function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

/**
 * Real-time Validation
 * Clear errors as user types
 */
document.addEventListener('input', function(e) {
    const field = e.target;
    if (field.classList.contains('form-input') || 
        field.classList.contains('form-select') || 
        field.classList.contains('form-textarea')) {
        clearError(field);
    }
});

/**
 * Phone Number Formatting
 * Auto-format phone number as user types
 */
document.addEventListener('input', function(e) {
    const field = e.target;
    if (field.name && field.name.includes('phone')) {
        let value = field.value.replace(/\D/g, '');
        
        if (value.startsWith('998')) {
            value = '+' + value;
            if (value.length > 4) value = value.slice(0, 4) + ' ' + value.slice(4);
            if (value.length > 7) value = value.slice(0, 7) + ' ' + value.slice(7);
            if (value.length > 11) value = value.slice(0, 11) + ' ' + value.slice(11);
            if (value.length > 14) value = value.slice(0, 14) + ' ' + value.slice(14);
            if (value.length > 17) value = value.slice(0, 17);
        }
        
        field.value = value;
    }
});

/**
 * Smooth Scroll to Errors
 */
window.addEventListener('load', function() {
    const firstError = document.querySelector('.field-errors');
    if (firstError) {
        setTimeout(() => {
            firstError.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }, 100);
    }
});
