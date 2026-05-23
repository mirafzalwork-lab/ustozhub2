# Бэкапы PostgreSQL — UstozHub

## Что бэкапится

Полный дамп БД `ustozhub` через `pg_dump`, формат **custom** (`-Fc`) — компактный,
параллельный restore, выбор отдельных таблиц при необходимости.

## Где лежат

`/var/backups/postgres/ustozhub-YYYY-MM-DD-HHMMSS.dump.gz`

Ротация: автоматическое удаление старше **14 дней**.

## Когда запускается

`/etc/cron.d/ustozhub-backup` → ежедневно в **03:00 по серверному времени** (Asia/Tashkent на проде).

## Восстановление (runbook)

### Полное восстановление с нуля

```bash
# 1. Подключиться к серверу
ssh root@164.92.185.36

# 2. Выбрать дамп (последний по умолчанию)
ls -la /var/backups/postgres/ | tail
DUMP=/var/backups/postgres/ustozhub-2026-05-23-030000.dump.gz

# 3. ОСТАНОВИТЬ веб (чтобы не было записей во время restore)
systemctl stop gunicorn daphne celery celery-beat

# 4. Бэкап ТЕКУЩЕЙ БД (на всякий — её НЕ удаляем без бэкапа)
sudo -u postgres pg_dump -Fc ustozhub > /tmp/pre-restore-$(date +%s).dump

# 5. Пересоздать БД
sudo -u postgres psql <<SQL
DROP DATABASE IF EXISTS ustozhub_old;
ALTER DATABASE ustozhub RENAME TO ustozhub_old;
CREATE DATABASE ustozhub OWNER ustozhub;
SQL

# 6. Восстановить из дампа
gunzip -c "$DUMP" | sudo -u postgres pg_restore --no-owner --role=ustozhub -d ustozhub

# 7. Запустить веб
systemctl start gunicorn daphne celery celery-beat

# 8. Проверить
curl -sI https://ustozhubedu.uz/ru/login/ | head -2

# 9. Если всё ОК — удалить старую
sudo -u postgres psql -c "DROP DATABASE ustozhub_old"
```

### Восстановление одной таблицы

```bash
# Список таблиц в дампе
gunzip -c $DUMP | pg_restore -l | grep "TABLE DATA"

# Restore только одной таблицы (например, teachers_booking)
gunzip -c $DUMP | pg_restore -t teachers_booking -d ustozhub --data-only --no-owner
```

## Что НЕ бэкапится (TODO)

- **Media-файлы** (R2/S3 bucket) — нужен отдельный sync или включить R2 versioning.
- **Offsite** — сейчас бэкапы лежат на том же дроплете, что и БД. При полной потере
  дроплета — потеряются и бэкапы. План: добавить ежедневный upload в R2
  (`rclone` или `aws s3 cp`). См. TODO ниже.

## Мониторинг

Логи cron:
```bash
journalctl -u cron.service --since today | grep ustozhub-backup
tail -50 /var/log/ustozhub-backup.log
```

Если последний бэкап старше 26 часов — что-то сломалось:
```bash
find /var/backups/postgres -name "ustozhub-*.dump.gz" -mtime -1 | head -1
# Должен возвращать хотя бы один файл
```

## TODO

- [ ] Offsite copy в R2 (нужно поставить `awscli` или `rclone` + ключи).
- [ ] Уведомлять админа в Telegram при провале бэкапа.
- [ ] Тестовый restore в staging-БД раз в месяц.
- [ ] Media-файлы на R2 — включить versioning в Cloudflare R2 console.
