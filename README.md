# РИ-фест

Сайт регистрации на Сибирский фестиваль ролевых игр «РИ-фест».

Django и PostgreSQL, серверные шаблоны и формы, стандартная Django Admin, обычный CSS и JavaScript.

## Запуск

Нужен Docker Desktop с Linux-контейнерами. Создайте `.env` по образцу [.env.example](.env.example) и заполните `DJANGO_SECRET_KEY`, `DB_PASSWORD` и `POSTGRES_ADMIN_PASSWORD`. Пароли базы должны различаться; значения со знаком `$` заключайте в одинарные кавычки.

```powershell
docker compose up --build -d
```

Сайт: <http://127.0.0.1:8000/>. Миграции применяются автоматически. После изменения кода повторите команду запуска.

`docker compose down` останавливает контейнеры, сохраняя данные в томах. PostgreSQL работает внутри Docker; локальная установка не нужна. Изменение пароля в `.env` не меняет пароль уже созданной роли базы.

## Проверки

```powershell
docker compose exec web python check.py
docker compose --profile test run --rm tests
docker compose exec web python manage.py makemigrations --check --dry-run
```

`check.py` запускает Django check, pytest и Ruff, включая проверку форматирования без перезаписи. Обычные тесты используют SQLite в памяти; сервис `tests` в профиле `test` проверяет работу с отдельной PostgreSQL-базой `ri_fest_test`.