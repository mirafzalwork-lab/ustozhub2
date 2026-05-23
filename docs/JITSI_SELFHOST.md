# Свой Jitsi для видео-уроков (без логина)

Публичный `meet.jit.si` требует входа через Google/GitHub у «модератора» —
это политика 8x8, обойти настройками нельзя. Решение — поднять собственный
сервер `docker-jitsi-meet` с `ENABLE_AUTH=0`: тогда участники заходят сразу,
без логина («как у Preply»).

Код приложения менять **не нужно** — только переменная окружения:
```
JITSI_BASE_URL=https://meet.ustozhubedu.uz
```
(см. `core/settings.py` → `JITSI_BASE_URL`, по умолчанию `https://meet.jit.si`).

---

## 0. Требования

- **Отдельный сервер** (рекомендуется): ≥ 2 ГБ RAM, 2 vCPU. Видео-трафик
  ресурсоёмкий — не вешать на тот же дроплет, что и сайт, если он маленький.
- Открытые порты: **TCP 80, 443** (веб + Let's Encrypt) и **UDP 10000**
  (медиа-трафик WebRTC). Без UDP 10000 звонок «соединяется» и обрывается.
- Поддомен **`meet.ustozhubedu.uz`** с A-записью на IP сервера.
- ⚠️ В проекте включён `SECURE_HSTS_INCLUDE_SUBDOMAINS = True` → у поддомена
  **обязан** быть валидный HTTPS-сертификат (Let's Encrypt ниже), иначе браузер
  откажется открывать комнату.

---

## 1. DNS

В панели домена добавить запись:
```
Тип: A    Имя: meet    Значение: <IP сервера Jitsi>    TTL: 300
```
Проверить: `dig +short meet.ustozhubedu.uz` → должен вернуть IP.

---

## 2. Docker + docker-jitsi-meet

```bash
# Docker (если не установлен)
curl -fsSL https://get.docker.com | sh

# Стабильный релиз
sudo git clone https://github.com/jitsi/docker-jitsi-meet.git /opt/jitsi
cd /opt/jitsi
sudo cp env.example .env

# Сгенерировать внутренние пароли компонентов
sudo ./gen-passwords.sh

# Каталоги конфигов
mkdir -p ~/.jitsi-meet-cfg/{web,transcripts,prosody/config,prosody/prosody-plugins-custom,jicofo,jvb,jigasi,jibri}
```

---

## 3. Настроить `.env` Jitsi

Открыть `/opt/jitsi/.env` и задать:

```ini
# Публичный адрес
PUBLIC_URL=https://meet.ustozhubedu.uz

# Порты (если на сервере свободны 80/443)
HTTP_PORT=80
HTTPS_PORT=443

# Let's Encrypt — валидный HTTPS (обязателен из-за HSTS includeSubDomains)
ENABLE_LETSENCRYPT=1
LETSENCRYPT_DOMAIN=meet.ustozhubedu.uz
LETSENCRYPT_EMAIL=admin@ustozhubedu.uz

# ❗ Без логина: гость сразу становится участником/модератором
ENABLE_AUTH=0
ENABLE_GUESTS=1

# Часовой пояс (для логов)
TZ=Asia/Tashkent

# Внешний IP для медиа (если сервер за NAT — публичный IP)
# JVB_ADVERTISE_IPS=<публичный IP сервера>
```

> Приватность: при `ENABLE_AUTH=0` зайти может любой, кто знает URL комнаты.
> Наши комнаты называются `UstozHub-<uuid4>` — UUID неугадываем, поэтому для
> MVP это безопасно. Усилить позже можно через JWT (`ENABLE_AUTH=1` + token).

---

## 4. Запуск

```bash
cd /opt/jitsi
sudo docker compose up -d

# Проверить, что контейнеры живы
sudo docker compose ps

# Логи (Let's Encrypt получает сертификат при старте)
sudo docker compose logs -f web
```

Открыть `https://meet.ustozhubedu.uz` в браузере → должна создаться тестовая
комната **без запроса логина**. Если просит логин — значит `ENABLE_AUTH` не 0
(перечитать `.env`, `docker compose down && up -d`).

---

## 5. Firewall

```bash
# ufw (Ubuntu)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 10000/udp
```
На облаке (DigitalOcean/AWS) — те же порты открыть в Cloud Firewall / Security
Group. **UDP 10000 — критично**, без него видео не пойдёт.

---

## 6. Подключить к приложению

В `.env` **прода UstozHub**:
```ini
JITSI_BASE_URL=https://meet.ustozhubedu.uz
```
Перезапустить веб-процесс (gunicorn/daphne), напр.:
```bash
sudo systemctl restart ustozhub   # или ваш сервис
```

Код не меняется: `Booking.build_meeting_url()` подставит новый домен,
`lesson_room` отдаст `external_api.js` уже с вашего сервера.

> Существующие подтверждённые брони хранят старый `meet.jit.si`-URL в БД.
> Их можно разово перегенерировать (для будущих уроков):
> ```python
> # manage.py shell
> from teachers.models import Booking
> from django.utils import timezone
> for b in Booking.objects.filter(status='confirmed', slot__start_at__gte=timezone.now()):
>     if 'meet.jit.si' in (b.meeting_url or ''):
>         b.meeting_url = b.build_meeting_url(); b.save(update_fields=['meeting_url'])
> ```

---

## 7. Проверка результата

1. Создать тестовую бронь, подтвердить (учителем).
2. В окне урока (за 15 мин до начала) нажать «Войти в урок».
3. Комната должна открыться **сразу, без Google/GitHub-логина**, видео и звук
   работают с двух устройств.

---

## Тюнинг (опционально)

- **Нагрузка**: один JVB тянет десятки параллельных участников на 2 vCPU.
  Для роста — добавить JVB-инстансы (горизонтальное масштабирование).
- **Запись уроков**: контейнер `jibri` (нужен ещё RAM + хранилище).
- **JWT-доступ** (строгая приватность, только наши пользователи): `ENABLE_AUTH=1`,
  `ENABLE_JWT=1`, выдавать токен из бэкенда — потребует доработки `lesson_room`.
