from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from applications.models import (
    Application,
    ApplicationAnswer,
    ApplicationServiceSelection,
    FeePeriod,
    FormField,
    FormOption,
    ServiceOption,
)
from billing.calculator import calc_application_total, calendar_cutoff, make_snapshot
from billing.models import FeeLock
from festivals.models import Festival

DEMO_FESTIVAL_NAMES = (
    "РИ-фест: Первый ролевой",
    "РИ-фест 2027: Первый ролевой",
    "Северный ветер",
    "Северный ветер: игры у озера",
    "Мастерская миров",
    "Осенние встречи",
)

PEOPLE = [
    ("Алексей", "Смирнов"),
    ("Мария", "Иванова"),
    ("Дмитрий", "Кузнецов"),
    ("Анна", "Попова"),
    ("Илья", "Соколов"),
    ("Екатерина", "Лебедева"),
    ("Максим", "Козлов"),
    ("Полина", "Новикова"),
    ("Артём", "Морозов"),
    ("Ольга", "Волкова"),
    ("Никита", "Зайцев"),
    ("Софья", "Павлова"),
    ("Егор", "Семёнов"),
    ("Вера", "Голубева"),
    ("Кирилл", "Виноградов"),
    ("Лена", "Беляева"),
    ("Роман", "Комаров"),
    ("Дарья", "Орлова"),
    ("Тимур", "Андреев"),
    ("Алиса", "Макарова"),
    ("Павел", "Никитин"),
    ("Миша", "Захаров"),
    ("Ксения", "Соловьёва"),
    ("Глеб", "Борисов"),
    ("Юля", "Яковлева"),
    ("Степан", "Григорьев"),
    ("Надя", "Романова"),
    ("Лев", "Воробьёв"),
    ("Ирина", "Сергеева"),
    ("Фёдор", "Медведев"),
    ("Маша", "Ершова"),
    ("Влад", "Киселёв"),
    ("Саша", "Тихонов"),
    ("Лиза", "Фёдорова"),
    ("Марк", "Михайлов"),
    ("Нина", "Баранова"),
]

ABOUT = [
    "Играю несколько лет, люблю камерные сюжеты и знакомиться с людьми. Могу помочь с реквизитом.",
    "Буду впервые. Пока присматриваюсь к программе, но на настолки и мастерскую точно загляну.",
    "Еду с подругой на машине. Если кому-то по пути из города, есть одно свободное место.",
    "Вожу игры и иногда пишу сценарии. Могу провести короткую игру вечером, если будет запрос.",
    "Хочется сменить обстановку, погулять и провести выходные без телефона. Особых пожеланий нет.",
    "Участвовал(а) в прошлом году — понравилась дружелюбная атмосфера. В этот раз приеду пораньше.",
    "Люблю мастерить: могу привезти материалы и показать, как делать простые кожаные брелоки.",
    "Пока не знаю, на сколько получится приехать из-за работы. Уточню даты ближе к событию.",
]


class Command(BaseCommand):
    help = "Заменить фестивали и анкеты реалистичными демонстрационными данными."

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=("reset", "add", "refresh"),
            default="refresh",
            help="reset: очистить базу (только DEBUG); add: добавить демо; refresh: обновить демо.",
        )
        parser.add_argument(
            "--apply", action="store_true", help="Выполнить выбранный режим."
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Только показать план."
        )

    def handle(self, *args, **options):
        mode = options["mode"]
        if mode == "reset" and not settings.DEBUG:
            raise CommandError("Режим reset разрешён только при DEBUG=True.")
        if not options["apply"] or options["dry_run"]:
            self.stdout.write(
                f"Режим {mode}: "
                + {
                    "reset": "полностью очистить базу и создать демо-пользователей и данные",
                    "add": "добавить демо-пользователей и данные без удаления текущих",
                    "refresh": "заменить фестивали с демонстрационными названиями и все связанные с ними анкеты; аккаунты сохраняются",
                }[mode]
                + ". Для выполнения добавьте --apply."
            )
            if mode == "refresh":
                festivals = list(
                    Festival.objects.filter(name__in=DEMO_FESTIVAL_NAMES)
                    .order_by("pk")
                    .values_list("pk", "name")
                )
                self.stdout.write(
                    f"Найдено фестивалей: {len(festivals)}, их анкет: "
                    f"{Application.objects.filter(festival_id__in=[pk for pk, _ in festivals]).count()}."
                )
                for pk, name in festivals:
                    self.stdout.write(f"  {pk}: {name}")
            return

        with transaction.atomic():
            if mode == "reset":
                from django.core.management import call_command

                call_command("flush", interactive=False, verbosity=0)

            if mode == "refresh":
                festival_ids = list(
                    Festival.objects.select_for_update()
                    .filter(name__in=DEMO_FESTIVAL_NAMES)
                    .order_by("pk")
                    .values_list("pk", flat=True)
                )
                Application.objects.filter(festival_id__in=festival_ids).delete()
                FormField.objects.filter(festival_id__in=festival_ids).delete()
                Festival.objects.filter(pk__in=festival_ids).delete()

            admin, admin_created = User.objects.get_or_create(
                email="admin@example.com",
                defaults={
                    "first_name": "Администратор",
                    "last_name": "Демо",
                    "is_staff": True,
                    "is_superuser": True,
                    "is_active": True,
                },
            )
            if admin_created:
                admin.set_password("admin")
                admin.save(update_fields=["password"])
                group, group_created = Group.objects.get_or_create(name="Организаторы")
                if group_created:
                    group.permissions.add(
                        *Permission.objects.filter(
                            Q(content_type__app_label="festivals")
                            | Q(
                                content_type__app_label="applications",
                                content_type__model__in=[
                                    "formfield",
                                    "formoption",
                                    "serviceoption",
                                ],
                            )
                        )
                    )
                admin.groups.add(group)

            participants = []
            for index, (first, last) in enumerate(PEOPLE, 1):
                email = f"demo-participant-{index:02d}@example.invalid"
                user, created = User.objects.get_or_create(
                    email=email,
                    defaults={
                        "first_name": first,
                        "last_name": last,
                        "is_active": True,
                    },
                )
                if created:
                    user.set_password("Demo-RiFest-2027!")
                    user.save()
                participants.append(user)

            today = timezone.localdate()
            specs = [
                (
                    "РИ-фест: Первый ролевой",
                    "open",
                    date(2027, 1, 28),
                    date(2027, 1, 31),
                ),
                (
                    "Северный ветер",
                    "open",
                    today + timedelta(days=60),
                    today + timedelta(days=63),
                ),
                (
                    "Мастерская миров",
                    "draft",
                    today + timedelta(days=90),
                    today + timedelta(days=92),
                ),
                (
                    "Осенние встречи",
                    "archived",
                    today - timedelta(days=23),
                    today - timedelta(days=20),
                ),
            ]
            for fi, (name, status, start, end) in enumerate(specs):
                festival = Festival.objects.create(
                    name=name,
                    theme="Ролевые игры, общение и совместное творчество.",
                    status=status,
                    start_date=start,
                    end_date=end,
                    published_at=None if status == "draft" else timezone.now(),
                )
                fields = {}

                def field(key, kind, label, **kwargs):
                    fields[key] = FormField.objects.create(
                        festival=festival,
                        field_type=kind,
                        label=label,
                        order=len(fields),
                        **kwargs,
                    )
                    return fields[key]

                if fi == 0:
                    field(
                        "welcome",
                        "info",
                        "Добро пожаловать на Первый ролевой",
                        help_text="Заезд 28 января после 15:00. Возраст участников — 18+.",
                    )
                    field(
                        "name",
                        "text",
                        "Имя или игровой псевдоним",
                        is_required=True,
                        in_application_name=True,
                    )
                    field("about", "textarea", "Что вам интересно на фестивале?")
                    field("age", "number", "Возраст", is_required=True)
                    field("guests", "number", "Сколько сопровождающих приедет?")
                    field("arrival", "date", "Когда планируете приехать?")
                    field("volunteer", "boolean", "Можете помочь на площадке?")
                    field(
                        "rules",
                        "boolean",
                        "Я ознакомился(-ась) с правилами",
                        is_required=True,
                    )
                    field(
                        "organizer_note",
                        "text",
                        "Заметка организатора",
                        is_active=False,
                    )
                    choice = field("transport", "single", "Как доберётесь?")
                    choice_options = [
                        FormOption.objects.create(field=choice, label=x, order=i)
                        for i, x in enumerate(
                            ["Автобус из города", "На своей машине", "Пока не решил(а)"]
                        )
                    ]
                    multi = field("interests", "multi", "Что хотите попробовать?")
                    multi_options = [
                        FormOption.objects.create(field=multi, label=x, order=i)
                        for i, x in enumerate(
                            [
                                "Ролевые игры",
                                "Мастерские",
                                "Настольные игры",
                                "Провести свою активность",
                            ]
                        )
                    ]
                    lodging = field(
                        "lodging", "service1", "Проживание — 2 070 ₽ за ночь"
                    )
                    stay = [
                        ServiceOption.objects.create(
                            field=lodging,
                            label=f"Ночь {start + timedelta(days=i):%d.%m}",
                            service_date=start + timedelta(days=i),
                            price=Decimal("2070.00"),
                            order=i,
                        )
                        for i in range(3)
                    ]
                    meals = field("meals", "service", "Питание")
                    meal_options = [
                        ServiceOption.objects.create(
                            field=meals,
                            label=label,
                            service_date=start + timedelta(days=day),
                            price=Decimal(price),
                            order=i,
                        )
                        for i, (day, label, price) in enumerate(
                            [
                                (1, "Пятница, обед", "450"),
                                (1, "Пятница, ужин", "400"),
                                (2, "Суббота, завтрак", "250"),
                                (2, "Суббота, обед", "450"),
                                (2, "Суббота, ужин", "400"),
                            ]
                        )
                    ]
                    meal_options.append(
                        ServiceOption.objects.create(
                            field=meals,
                            label="Питаюсь самостоятельно",
                            is_opt_out=True,
                            order=5,
                        )
                    )
                    fee = field("fee", "datefee", "Организационный взнос")
                    for ends_on, amount in [
                        (date(2026, 11, 30), "1300.00"),
                        (date(2027, 1, 10), "1400.00"),
                        (date(2027, 1, 27), "1600.00"),
                        (None, "1800.00"),
                    ]:
                        FeePeriod.objects.create(
                            field=fee, ends_on=ends_on, amount=Decimal(amount)
                        )
                elif fi == 1:
                    field(
                        "name",
                        "text",
                        "Как к вам обращаться?",
                        is_required=True,
                        in_application_name=True,
                    )
                    field("about", "textarea", "Расскажите, с каким настроением едете")
                    field("arrival", "date", "День приезда")
                    field("camp", "boolean", "Планирую жить в палатке")
                    field(
                        "rules",
                        "boolean",
                        "Согласен(-на) с правилами лагеря",
                        is_required=True,
                    )
                    field(
                        "organizer_note",
                        "text",
                        "Заметка организатора",
                        is_active=False,
                    )
                    choice = field("role", "single", "Что вам ближе?")
                    choice_options = [
                        FormOption.objects.create(field=choice, label=x, order=i)
                        for i, x in enumerate(["Игрок", "Мастер", "Помощник"])
                    ]
                    multi = field("food", "multi", "Какие активности интересны?")
                    multi_options = [
                        FormOption.objects.create(field=multi, label=x, order=i)
                        for i, x in enumerate(
                            [
                                "Полевые игры",
                                "Поход к озеру",
                                "Костёр и песни",
                                "Обмен настольными играми",
                            ]
                        )
                    ]
                    lodging = field("lodging", "service1", "Место в гостевом доме")
                    stay = [
                        ServiceOption.objects.create(
                            field=lodging,
                            label="Ночь в общей комнате",
                            price=Decimal("1800"),
                        ),
                        ServiceOption.objects.create(
                            field=lodging,
                            label="Без проживания",
                            is_opt_out=True,
                            order=1,
                        ),
                    ]
                    meals = field("meals", "service", "Общие ужины")
                    meal_options = [
                        ServiceOption.objects.create(
                            field=meals, label=x, price=Decimal("350"), order=i
                        )
                        for i, x in enumerate(["Ужин в пятницу", "Ужин в субботу"])
                    ]
                    meal_options.append(
                        ServiceOption.objects.create(
                            field=meals,
                            label="Не участвовать в общих ужинах",
                            is_opt_out=True,
                            order=2,
                        )
                    )
                    fee = field("fee", "datefee", "Взнос участника")
                    for ends_on, amount in [
                        (today + timedelta(days=30), "900.00"),
                        (None, "1100.00"),
                    ]:
                        FeePeriod.objects.create(
                            field=fee, ends_on=ends_on, amount=Decimal(amount)
                        )
                elif fi == 2:
                    field(
                        "name",
                        "text",
                        "Имя для бейджа",
                        is_required=True,
                        in_application_name=True,
                    )
                    field("about", "textarea", "Чему хотите научиться?")
                    field("age", "number", "Возраст")
                    multi = field("craft", "multi", "Какие мастерские выбрать?")
                    multi_options = [
                        FormOption.objects.create(field=multi, label=x, order=i)
                        for i, x in enumerate(
                            [
                                "Рисование карты",
                                "Основы вёрстки буклета",
                                "Создание персонажа",
                                "Командная игра по вечерам",
                            ]
                        )
                    ]
                    choice_options = []
                    stay = meal_options = []
                else:
                    field(
                        "name",
                        "text",
                        "Имя участника",
                        is_required=True,
                        in_application_name=True,
                    )
                    field("about", "textarea", "Что запомнилось больше всего?")
                    multi = field("return", "multi", "Что повторить в следующий раз?")
                    multi_options = [
                        FormOption.objects.create(field=multi, label=x, order=i)
                        for i, x in enumerate(
                            ["Игры", "Мастерские", "Общий ужин", "Прогулка"]
                        )
                    ]
                    choice_options = []
                    stay = meal_options = []
                    fee = field("fee", "datefee", "Взнос участника")
                    FeePeriod.objects.create(
                        field=fee, ends_on=None, amount=Decimal("700.00")
                    )
                if status == "draft":
                    continue

                owners = participants[:12] if fi != 1 else participants
                for pi, owner in enumerate(owners):
                    app_status = (
                        "archived"
                        if status == "archived"
                        else (
                            "draft"
                            if fi == 1 and pi == 0
                            else "withdrawn"
                            if fi == 1 and pi in (2, 9, 17, 28)
                            else "active"
                        )
                    )
                    app = Application.objects.create(
                        owner=owner, festival=festival, status=app_status
                    )
                    if status == "draft" or app_status == "draft":
                        continue
                    display_name = f"{owner.first_name} {owner.last_name}"
                    if fi == 1 and pi == 0:
                        display_name = "Лисёнок"  # Второе имя для того же пользователя.
                    answers = {
                        "name": display_name,
                        "about": ABOUT[pi % len(ABOUT)],
                        "rules": pi % 8 != 6,
                    }
                    if "age" in fields:
                        answers["age"] = 19 + (pi * 7) % 43
                    if "guests" in fields and pi % 4:
                        answers["guests"] = pi % 3
                    if "arrival" in fields and pi % 5:
                        answers["arrival"] = (
                            start + timedelta(days=pi % 3)
                        ).isoformat()
                    if "volunteer" in fields:
                        answers["volunteer"] = pi % 4 == 0
                    if "camp" in fields:
                        answers["camp"] = pi % 3 == 0
                    if "transport" in fields or "role" in fields:
                        key = "transport" if "transport" in fields else "role"
                        if pi % 6 != 5:
                            answers[key] = str(
                                choice_options[pi % len(choice_options)].pk
                            )
                    multi_key = (
                        "interests"
                        if "interests" in fields
                        else "food"
                        if "food" in fields
                        else "craft"
                        if "craft" in fields
                        else "return"
                    )
                    if pi % 7:
                        answers[multi_key] = (
                            [str(o.pk) for o in multi_options]
                            if pi % 5 == 0
                            else [
                                str(o.pk)
                                for o in multi_options[: pi % len(multi_options)]
                            ]
                            if pi % 4
                            else []
                        )
                    if pi % 6 == 4:
                        answers.pop("about", None)
                    if "organizer_note" in fields:
                        answers["organizer_note"] = (
                            "Уточнить время приезда"
                            if pi % 2
                            else "Постоянный участник"
                        )
                    answers = {
                        key: value for key, value in answers.items() if key in fields
                    }
                    ApplicationAnswer.objects.bulk_create(
                        [
                            ApplicationAnswer(application=app, field=fields[k], value=v)
                            for k, v in answers.items()
                        ]
                    )
                    selected = []
                    if stay:
                        selected.append(stay[0] if pi % 4 != 3 else stay[-1])
                    if meal_options:
                        selected.extend(
                            [meal_options[-1]]
                            if pi % 5 == 3
                            else [
                                meal_options[j]
                                for j in range(len(meal_options) - 1)
                                if (j + pi) % 3 == 0
                            ]
                        )
                    ApplicationServiceSelection.objects.bulk_create(
                        [
                            ApplicationServiceSelection(application=app, option=o)
                            for o in selected
                        ]
                    )
                    if pi % 3 == 0 and "fee" in fields:
                        FeeLock.objects.create(
                            application=app,
                            field=fields["fee"],
                            amount=Decimal("1300.00"),
                            locked_at=timezone.now(),
                        )
                    total = calc_application_total(app)["total"]
                    app.paid_amount = (
                        Decimal("0")
                        if pi % 4 == 0
                        else total / 2
                        if pi % 4 == 1
                        else total
                        if pi % 4 == 2
                        else total + Decimal("500")
                    ).quantize(Decimal("0.01"))
                    app.was_submitted = app_status != "draft"
                    if status == "archived":
                        app.financial_snapshot = make_snapshot(
                            app, at=calendar_cutoff(festival)
                        )
                        app.financial_snapshot_current = True
                    app.save(
                        update_fields=(
                            "paid_amount",
                            "was_submitted",
                            "financial_snapshot",
                            "financial_snapshot_current",
                        )
                    )
                self.stdout.write(f"Создан фестиваль и анкеты: {name}")

        self.stdout.write(self.style.SUCCESS("Демо-данные заменены."))
