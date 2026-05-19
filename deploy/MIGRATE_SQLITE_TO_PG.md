# Безопасная миграция SQLite → PostgreSQL на live droplet

**Сервер:** 164.92.185.36
**Принцип:** каждый шаг проверяемый, rollback на любом этапе — 30 сек.

## Что мы делаем

1. Бэкапим всё.
2. На текущей SQLite применяем новые миграции (0024 — бэкфилл `search_text`, `viewed_date`, `WizardDraft`).
3. Дампим данные в JSON.
4. Поднимаем PostgreSQL.
5. Переключаем `DATABASE_URL` → грузим JSON в PG.
6. Проверяем, рестартуем сервисы.

**Если что-то сломалось** на шаге 5–6 — `nano .env` → закомментировать `DATABASE_URL` → `systemctl restart …` → сайт снова на SQLite. Все данные целы.

---

## Шаг 0. SSH и pre-flight

```bash
ssh user@164.92.185.36
cd /var/www/ustozhubuz   # или где у тебя проект
source venv/bin/activate
```

Проверь, что сейчас работает SQLite:
```bash
python manage.py shell -c "from django.conf import settings; print(settings.DATABASES['default']['ENGINE'])"
# ожидаемо: django.db.backends.sqlite3
```

---

## Шаг 1. БЭКАП ВСЕГО (обязательно)

```bash
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p ~/ustozhub_backups
cp db.sqlite3 ~/ustozhub_backups/db.sqlite3.before_pg_$TS
tar -czf ~/ustozhub_backups/media_$TS.tar.gz media/
cp .env ~/ustozhub_backups/env_$TS.backup
ls -lh ~/ustozhub_backups/
```

Скачай бэкап на локалку (на всякий случай):
```bash
# С локальной машины:
scp user@164.92.185.36:~/ustozhub_backups/db.sqlite3.before_pg_* ~/Desktop/
scp user@164.92.185.36:~/ustozhub_backups/media_*.tar.gz ~/Desktop/
```

---

## Шаг 2. Обновить код (git pull) и зависимости

```bash
# На сервере:
git status                          # проверь что нет локальных правок
git pull origin main
pip install -r requirements.txt     # подтянет psycopg, redis, celery, sentry-sdk
```

---

## Шаг 3. Применить миграции к ТЕКУЩЕЙ SQLite

Это критично: миграция 0024 содержит RunPython, который **бэкфиллит** `search_text`, `viewed_date`, дедуп `ProfileView`. Если пропустить — данные после переноса будут с пустыми новыми полями.

```bash
python manage.py migrate
python manage.py check
```

Ожидаемо: миграция 0024 применится, бэкфилл займёт несколько секунд (у тебя ~150 учителей, ~70 предметов).

Проверь, что данные на месте:
```bash
python manage.py shell -c "
from teachers.models import User, TeacherProfile, Subject, ProfileView
print('Users:', User.objects.count())
print('Teachers:', TeacherProfile.objects.count())
print('Subjects:', Subject.objects.count())
print('ProfileViews:', ProfileView.objects.count())
print('Subjects с search_text:', Subject.objects.exclude(search_text='').count())
"
```

---

## Шаг 4. Дамп данных в JSON

```bash
python manage.py dumpdata \
  --natural-foreign --natural-primary \
  --exclude=contenttypes \
  --exclude=auth.permission \
  --exclude=sessions \
  --exclude=admin.logentry \
  --indent=2 \
  -o ~/ustozhub_backups/data_dump_$TS.json

ls -lh ~/ustozhub_backups/data_dump_$TS.json
```

Если дамп больше 100 MB — что-то не так (у тебя должно быть ~5–10 MB).

---

## Шаг 5. Установить PostgreSQL (если не стоит)

Проверь:
```bash
which psql && systemctl status postgresql --no-pager | head -3
```

Если нет — поставь:
```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib libpq-dev
sudo systemctl enable --now postgresql
```

---

## Шаг 6. Создать БД и пользователя

```bash
# СГЕНЕРИРУЙ СЛОЖНЫЙ ПАРОЛЬ, не используй пример:
DBPASS=$(openssl rand -base64 24 | tr -d '/+=')
echo "Сгенерирован пароль (сохрани!): $DBPASS"

sudo -u postgres psql <<EOF
CREATE DATABASE ustozhub;
CREATE USER ustozhub WITH ENCRYPTED PASSWORD '$DBPASS';
ALTER ROLE ustozhub SET client_encoding TO 'utf8';
ALTER ROLE ustozhub SET default_transaction_isolation TO 'read committed';
ALTER ROLE ustozhub SET timezone TO 'Asia/Tashkent';
GRANT ALL PRIVILEGES ON DATABASE ustozhub TO ustozhub;
\c ustozhub
GRANT ALL ON SCHEMA public TO ustozhub;
EOF

# Проверка подключения:
PGPASSWORD="$DBPASS" psql -h localhost -U ustozhub -d ustozhub -c "SELECT version();"
```

Запомни `$DBPASS` — он нужен в следующем шаге. **Не теряй его** и **не коммить в git**.

---

## Шаг 7. Прописать DATABASE_URL в .env

```bash
nano .env
```

Добавь (замени `СГЕНЕРИРОВАННЫЙ_ПАРОЛЬ`):
```env
DATABASE_URL=postgres://ustozhub:СГЕНЕРИРОВАННЫЙ_ПАРОЛЬ@localhost:5432/ustozhub
DB_SSLMODE=disable

# Обязательно явно укажи хосты для прода (мы убрали '*' из defaults):
ALLOWED_HOSTS=ustozhubedu.uz,www.ustozhubedu.uz,164.92.185.36,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://ustozhubedu.uz,https://www.ustozhubedu.uz,http://164.92.185.36

# Redis (если ещё не задано):
REDIS_URL=redis://127.0.0.1:6379
USE_REDIS_CACHE=True

# Celery — в проде НЕ eager (нужен реальный worker):
CELERY_TASK_ALWAYS_EAGER=False
```

---

## Шаг 8. Создать схему в PG и загрузить данные

```bash
# Создаём схему в новой PG БД:
python manage.py migrate
python manage.py check

# Загружаем данные из дампа:
python manage.py loaddata ~/ustozhub_backups/data_dump_$TS.json
```

Если `loaddata` упал — читай ошибку. Самые частые:
- **IntegrityError**: попробуй сначала очистить PG (`python manage.py flush --noinput`) и `migrate` заново, потом снова `loaddata`.
- **DoesNotExist (ContentType)**: добавь в exclude дампа `--exclude=auth.permission --exclude=contenttypes` (уже добавлено).

Проверь:
```bash
python manage.py shell -c "
from django.conf import settings
print('DB:', settings.DATABASES['default']['ENGINE'])
from teachers.models import User, TeacherProfile, Subject
print('Users:', User.objects.count())
print('Teachers:', TeacherProfile.objects.count())
print('Subjects:', Subject.objects.count())
"
```

Цифры должны совпасть с шагом 3.

---

## Шаг 9. collectstatic + перезапуск

```bash
python manage.py collectstatic --noinput

sudo systemctl restart gunicorn
sudo systemctl restart daphne
sudo systemctl restart telegram-bot
sudo systemctl status gunicorn daphne telegram-bot --no-pager | head -30
```

Проверь сайт:
```bash
curl -sI http://164.92.185.36/ | head -5
curl -sI https://ustozhubedu.uz/ | head -5
```

Открой в браузере, прокликай:
- главная
- профиль учителя
- логин
- чат

---

## Шаг 10. Celery worker (новый сервис)

Создай `/etc/systemd/system/celery.service`:

```ini
[Unit]
Description=UstozHub Celery Worker
After=network.target redis.service postgresql.service

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/var/www/ustozhubuz
EnvironmentFile=/var/www/ustozhubuz/.env
ExecStart=/var/www/ustozhubuz/venv/bin/celery -A core worker -l info --concurrency=2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

И `/etc/systemd/system/celery-beat.service`:

```ini
[Unit]
Description=UstozHub Celery Beat
After=network.target redis.service postgresql.service

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/var/www/ustozhubuz
EnvironmentFile=/var/www/ustozhubuz/.env
ExecStart=/var/www/ustozhubuz/venv/bin/celery -A core beat -l info
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Запуск:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now celery celery-beat
sudo systemctl status celery celery-beat --no-pager | head -20
```

Проверка задачи:
```bash
cd /var/www/ustozhubuz && source venv/bin/activate
python manage.py shell -c "
from teachers.tasks import health_check
r = health_check.delay()
print('Task id:', r.id)
print('Result:', r.get(timeout=5))
"
```

---

## ROLLBACK (если что-то пошло не так)

В любой момент шагов 7–10:

```bash
nano .env
# закомментируй строку с DATABASE_URL:
# DATABASE_URL=postgres://...

sudo systemctl restart gunicorn daphne telegram-bot
```

Сайт мгновенно вернётся на SQLite (`db.sqlite3`). Никакие данные не теряются — SQLite-файл нетронут.

Если SQLite файл повреждён — восстанови из бэкапа:
```bash
cp ~/ustozhub_backups/db.sqlite3.before_pg_<timestamp> db.sqlite3
sudo systemctl restart gunicorn daphne telegram-bot
```

---

## Финальная проверка после миграции

```bash
# 1. Логи без ошибок?
sudo journalctl -u gunicorn -n 50 --no-pager | grep -i error
sudo journalctl -u daphne -n 50 --no-pager | grep -i error
sudo journalctl -u celery -n 50 --no-pager | grep -i error

# 2. БД содержит данные?
python manage.py shell -c "
from teachers.models import *
print('Users:', User.objects.count())
print('Teachers approved:', TeacherProfile.objects.filter(moderation_status='approved').count())
print('Conversations:', Conversation.objects.count())
print('Messages:', Message.objects.count())
print('Reviews:', Review.objects.count())
print('Notifications:', Notification.objects.count())
"

# 3. Celery видит задачи?
python manage.py shell -c "
from core.celery import app
print('Tasks:', sorted([t for t in app.tasks if 'teachers' in t]))
"
```

---

## После успешной миграции

1. **Не удаляй** `~/ustozhub_backups/` минимум неделю.
2. **Ротируй** все секреты, которые были в `.env` (`SECRET_KEY`, `TELEGRAM_BOT_TOKEN`, `GOOGLE_CLIENT_SECRET`, S3 ключи).
3. **Сделай настройку автобэкапов** PG (отдельная задача):
   ```bash
   # /etc/cron.daily/pg_backup.sh
   sudo -u postgres pg_dump ustozhub | gzip > /var/backups/pg/ustozhub_$(date +\%F).sql.gz
   ```
