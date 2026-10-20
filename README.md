# РИ-фест

Сайт регистрации на Сибирский фестиваль ролевых игр «РИ-фест».

Стек: Django, PostgreSQL, Django Templates, Forms/ModelForms, Django Admin, обычный CSS и JavaScript.

Проект в разработке: подготовлены Django-каркас, стартовая страница и инструменты проверок. Регистрация, анкеты и учёт оплаты пока не реализованы.

## Локальный запуск

Нужны Conda и запущенный PostgreSQL с отдельной базой и пользователем проекта.

```powershell
conda env create --file environment.yml
conda activate ri_fest
```

Создайте `.env` по образцу [.env.example](.env.example), заполните `DJANGO_SECRET_KEY` и параметры `DB_*`. Для локальной разработки задайте `DJANGO_DEBUG=True`. Ключ можно сгенерировать командой:

```powershell
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Запустите сервер:

```powershell
python manage.py runserver
```

Сайт: <http://127.0.0.1:8000/>.


## Проверки

```powershell
python check.py
```

Django check, pytest, Ruff и проверка форматирования без изменения файлов.