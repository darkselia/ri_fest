# РИ-фест

Сайт регистрации на Сибирский фестиваль ролевых игр «РИ-фест»: Django, PostgreSQL, серверные шаблоны и формы, стандартная Django Admin, обычные CSS и JavaScript.

## Подготовка один раз

1. В корне проекта создайте `.env` по образцу [.env.example](.env.example).
2. Заполните `DJANGO_SECRET_KEY`, `DB_PASSWORD` и `POSTGRES_ADMIN_PASSWORD`. Пароли приложения и администратора БД должны различаться. Значения со знаком `$` заключайте в одинарные кавычки.
3. Для локальной разработки оставьте `DJANGO_DEBUG=True` и почтовые настройки Mailpit.

## Dev: повседневная разработка

### Первый запуск

```powershell
docker compose -f compose.yaml -f compose.dev.yaml --profile dev-mail up --build -d
```

Команда собирает образ и запускает PostgreSQL, Redis, сайт, Celery worker, единственный Beat и Mailpit. Сервис `migrate` применяет миграции перед запуском приложения.

- Сайт: <http://127.0.0.1:8000/>.
- Django Admin: <http://127.0.0.1:8000/admin/>.
- Локальные письма: <http://127.0.0.1:8025/>. Mailpit принимает их внутри Docker; наружу они не отправляются. При пересоздании Mailpit письма могут удалиться.

### Следующие запуски

Если зависимости и Dockerfile не менялись, сборка не нужна:

Исключение — изменения переводов в `locale/`: русский каталог админки компилируется при сборке образа и хранится вне `/app`, чтобы работать с dev-overlay. После изменения переводов пересоберите образ и обновите сервисы приложения.

```powershell
docker compose -f compose.yaml -f compose.dev.yaml --profile dev-mail up -d
```

Если контейнеры были только остановлены командой `stop`, продолжить их работу без пересоздания можно командой `start`, приведённой ниже.

При необходимости принудительно перезапустить только Django, сохранив контейнер:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml restart web
```

После изменения фонового кода:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml restart worker beat
```

После добавления миграций:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec web python manage.py migrate --noinput
```

### Остановка и продолжение работы

Остановить существующие контейнеры, сохранив их и данные:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml --profile dev-mail stop
```

Запустить те же контейнеры:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml --profile dev-mail start
```

`restart` перезапускает существующие контейнеры, `up` создаёт или пересоздаёт их при необходимости, `down` удаляет контейнеры и сеть проекта. Обычный `down` сохраняет именованные тома; добавление `--volumes` удаляет и их данные.

PostgreSQL, Redis и расписание Beat хранятся в именованных томах. Изменение паролей в `.env` не меняет пароли уже созданных ролей PostgreSQL.


## Проверки

Для уже запущенного dev-окружения:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml exec web python check.py
docker compose -f compose.yaml -f compose.dev.yaml --profile test run --rm tests
docker compose -f compose.yaml -f compose.dev.yaml exec web python manage.py makemigrations --check --dry-run
```

- `check.py`: Django check, pytest на SQLite в памяти, Ruff и проверка форматирования без перезаписи.
- Сервис `tests`: интеграционные проверки на отдельной PostgreSQL-базе `ri_fest_test`, Redis и SMTP через Mailpit. Профиль `test` сам подключает нужные зависимости.
- `makemigrations --check --dry-run`: проверка соответствия моделей и миграций без создания файлов.

Проверки выбираются по изменению согласно [AGENTS.md](AGENTS.md). Для правки документации приложение запускать не требуется; полный набор нужен при завершении контрольной точки и перед выпуском.

## Почта и подтверждение email

Для настоящей отправки заполните SMTP-переменные из [.env.example](.env.example): сервер, порт, логин, пароль, TLS/SSL и адрес отправителя. TLS и SSL одновременно не включаются. После изменения `.env` повторите `up -d` для выбранного режима и отдельно проверьте доставку. При использовании внешнего SMTP Mailpit не требуется.

В `ri_fest/settings.py` настройка `EMAIL_VERIFICATION_REQUIRED=False` разрешает вход после регистрации без подтверждения email. При `True` новый аккаунт сначала подтверждает адрес. Эта настройка не зависит от DEBUG и не задаётся через env. Она также определяет порядок смены email: сразу либо после подтверждения нового адреса.
