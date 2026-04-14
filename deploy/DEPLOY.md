# Deploy на Digital Ocean (Ubuntu 22.04+)

## Архитектура
- **Nginx** — reverse proxy + статика
- **Gunicorn** (HTTP/WSGI) → unix socket `/run/gunicorn.sock`
- **Daphne** (WebSockets/ASGI) → unix socket `/run/daphne.sock` (для `/ws/`)
- **Redis** — Channels layer
- **PostgreSQL** (рекомендуется вместо SQLite)
- **Telegram bot** — отдельный systemd сервис

## 1. Подготовка сервера
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv python3-dev nginx redis-server \
    postgresql postgresql-contrib libpq-dev git certbot python3-certbot-nginx
```

## 2. Клонирование проекта
```bash
sudo mkdir -p /var/www/ustozhubuz
sudo chown -R $USER:www-data /var/www/ustozhubuz
cd /var/www/ustozhubuz
git clone https://github.com/mirafzalwork-lab/ustozhub2.git .
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gunicorn psycopg2-binary
```

## 3. Настройка PostgreSQL
```bash
sudo -u postgres psql
CREATE DATABASE ustozhubuz;
CREATE USER ustozuser WITH PASSWORD 'strong-password';
ALTER ROLE ustozuser SET client_encoding TO 'utf8';
GRANT ALL PRIVILEGES ON DATABASE ustozhubuz TO ustozuser;
\q
```

## 4. Настройка .env
```bash
cp deploy/.env.example .env
nano .env  # заполни значения
```

## 5. Миграции и статика
```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

## 6. Логи
```bash
sudo mkdir -p /var/log/gunicorn /var/log/daphne
sudo chown www-data:www-data /var/log/gunicorn /var/log/daphne
```

## 7. Установка systemd сервисов
```bash
sudo cp deploy/gunicorn.socket /etc/systemd/system/
sudo cp deploy/gunicorn.service /etc/systemd/system/
sudo cp deploy/daphne.service /etc/systemd/system/
sudo cp deploy/telegram-bot.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now gunicorn.socket gunicorn.service daphne.service telegram-bot.service
sudo systemctl status gunicorn daphne telegram-bot
```

## 8. Nginx
```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/ustozhubuz
sudo ln -s /etc/nginx/sites-available/ustozhubuz /etc/nginx/sites-enabled/
sudo rm /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

## 9. SSL (Let's Encrypt)
```bash
sudo certbot --nginx -d ustozhubedu.uz -d www.ustozhubedu.uz
```

## 10. Permissions
```bash
sudo chown -R www-data:www-data /var/www/ustozhubuz/media /var/www/ustozhubuz/staticfiles
```

## Полезные команды
```bash
# Перезапуск после обновления кода
git pull
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart gunicorn daphne telegram-bot

# Логи
sudo journalctl -u gunicorn -f
sudo journalctl -u daphne -f
sudo journalctl -u telegram-bot -f
sudo tail -f /var/log/nginx/ustozhubuz_error.log
```

## Важно перед prod
- В `core/settings.py`: `DEBUG = False`, убрать `'*'` из `ALLOWED_HOSTS`
- Сгенерировать новый `SECRET_KEY`
- Перенести БД с SQLite на PostgreSQL (`pg_loader` или `dumpdata`/`loaddata`)
- Настроить бэкапы БД и `media/`
