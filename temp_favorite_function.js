

    // Toggle favorite student
    function toggleFavorite(studentId) {
        {% if user.is_authenticated and user.user_type == 'teacher' %}
            fetch(`/toggle-favorite-student/${studentId}/`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': '{{ csrf_token }}'
                }
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    location.reload();
                } else {
                    alert('{% trans "Произошла ошибка" %}');
                }
            })
            .catch(error => {
                console.error('Error:', error);
                alert('{% trans "Произошла ошибка при добавлении в избранное" %}');
            });
        {% else %}
            if (confirm('{% trans "Войдите в систему, чтобы добавить в избранное" %}')) {
                window.location.href = '{% url "login" %}?next={{ request.path }}';
            }
        {% endif %}
    }


