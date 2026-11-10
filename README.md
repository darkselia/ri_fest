# РИ-фест

Сайт регистрации на Сибирский фестиваль ролевых игр «РИ-фест»: Django, PostgreSQL, серверные шаблоны и формы, Django Admin с оформлением django-unfold, обычные CSS и JavaScript.

## Документация и текущий объём

Реализованы аккаунты, несколько фестивалей, конструктор анкет, ответы, услуги и тарифные блоки платы по дате. Главная пока заглушка. Учёт внесённых денег, индивидуальные фиксации, архивные финансовые снимки, XLSX, редактируемый публичный контент и уведомления организаторов ещё не реализованы. Предварительная сумма анкеты не означает подтверждённую оплату.

## Подготовка один раз

1. В корне проекта создайте `.env` по образцу [.env.example](.env.example).
2. Заполните `DJANGO_SECRET_KEY`, `DB_PASSWORD` и `POSTGRES_ADMIN_PASSWORD`. Пароли приложения и администратора БД должны различаться. Значения со знаком `$` заключайте в одинарные кавычки.
3. Для локальной разработки оставьте `DJANGO_DEBUG=True` и почтовые настройки Mailpit.
4. Для доступа с другого устройства добавьте IP или имя Docker-хоста в `DJANGO_ALLOWED_HOSTS` (через запятую).

## Dev: повседневная разработка

### Первый запуск

```powershell
docker compose -f compose.yaml -f compose.dev.yaml --profile dev-mail up --build -d
```

Команда собирает образ и запускает PostgreSQL, Redis, сайт, Celery worker, единственный Beat и Mailpit. Сервис `migrate` применяет миграции перед запуском приложения.

- Сайт: `http://<IP-компьютера>:8000/`; Django Admin: `http://<IP-компьютера>:8000/admin/`.
- Mailpit: `http://<IP-компьютера>:8025/`; SMTP для приложения и других устройств: `<IP-компьютера>:1025`. Письма остаются в Mailpit и наружу не отправляются. При пересоздании Mailpit письма могут удалиться.
- PostgreSQL: `<IP-компьютера>:5432` (база и роль приложения — `DB_NAME` и `DB_USER` из `.env`, пароль — `DB_PASSWORD`).
- Redis: `<IP-компьютера>:6379`.

В dev-конфигурации опубликованы порты Django, PostgreSQL, Redis и Mailpit на сетевые интерфейсы компьютера. Для подключения с другого устройства используйте IP компьютера, на котором запущен Docker; доступ также должен быть разрешён сетевым экраном.

### Следующие запуски

Если зависимости, Dockerfile и переводы не менялись, сборка не нужна:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml --profile dev-mail up -d
```

Исходники подключены в `/app` только для чтения, Django перезагружается автоматически. После изменения зависимостей или Dockerfile повторите команду первого запуска с `--build`. Переводы в `locale/` также требуют пересборки: скомпилированный каталог Admin хранится вне `/app`.

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
- Сервис `tests`: все сценарии с маркером `pytest.mark.postgres` на отдельной PostgreSQL-базе `ri_fest_test`, Redis и SMTP через Mailpit. Набор охватывает аккаунты, фестивали, оплату и конкурентные операции с анкетами. Новые сценарии, которым нужен PostgreSQL, также помечайте этим маркером: профиль `test` подхватит их автоматически и подключит нужные зависимости.
- `makemigrations --check --dry-run`: проверка соответствия моделей и миграций без создания файлов.

Проверки выбираются по изменению согласно [AGENTS.md](AGENTS.md). Для правки документации приложение запускать не требуется; полный набор нужен при завершении контрольной точки и перед выпуском.

## Отступы и dev-линтер

`.editorconfig` задаёт четыре пробела для Python, Django HTML, JS и CSS (два для YAML/JSON).
`Dockerfile.lint` расширяет образ приложения инструментами разработки; `tools/lint_format.py` объединяет проверки. Его сервис `lint` запускается только профилем `dev-tools`, а исходники подключены только для чтения. Изолированный dev-образ содержит djLint для Django-шаблонов и форматирующие проверки JS/CSS;
Python проверяется форматтером Ruff; без аргументов он проверяет проект, при переданных путях — найденные в них `.py`-файлы. Форматтеры расставляют пробелы вокруг операторов по правилам
соответствующего языка; в CSS комбинаторы `>`, `+` и `~` также отделяются пробелами.
Production-зависимости не меняются. Проверки ничего не перезаписывают:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml --profile dev-tools build lint
docker compose -f compose.yaml -f compose.dev.yaml --profile dev-tools run --rm lint
```

Можно проверить только изменённые файлы, передав их аргументами:

```powershell
docker compose -f compose.yaml -f compose.dev.yaml --profile dev-tools run --rm lint python tools/lint_format.py templates/admin/applications/edit.html static/js/admin_lists.js
```

Полная проверка также показывает старые расхождения форматирования. Правила Python-кода
дополнительно проверяются командой `ruff check` в `check.py`.

## Почта и подтверждение email

Для настоящей отправки заполните SMTP-переменные из [.env.example](.env.example): сервер, порт, логин, пароль, TLS/SSL и адрес отправителя. TLS и SSL одновременно не включаются. После изменения `.env` повторите `up -d` для выбранного режима и отдельно проверьте доставку. При использовании внешнего SMTP Mailpit не требуется.

В `ri_fest/settings.py` настройка `EMAIL_VERIFICATION_REQUIRED=False` разрешает вход после регистрации без подтверждения email. При `True` новый аккаунт сначала подтверждает адрес. Эта настройка не зависит от DEBUG и не задаётся через env. Она также определяет порядок смены email: сразу либо после подтверждения нового адреса.
