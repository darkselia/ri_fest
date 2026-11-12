from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
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
from festivals.models import Festival


class Command(BaseCommand):
    help = "Заменить фестивали и анкеты демонстрационными данными."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        if not options["apply"]:
            self.stdout.write(
                "Будут заменены все фестивали и анкеты. Для выполнения добавьте --apply."
            )
            return

        with transaction.atomic():
            # Clear dependent rows first; applications belonging to real users are replaced too.
            Application.objects.all().delete()
            FormField.objects.all().delete()
            Festival.objects.all().delete()

            admin, _ = User.objects.get_or_create(email="admin@example.com")
            admin.first_name, admin.last_name = "Администратор", "Демо"
            admin.is_staff = admin.is_superuser = True
            admin.is_active = True
            admin.set_password("admin")
            admin.save()

            group, created = Group.objects.get_or_create(name="Организаторы")
            group.permissions.add(
                *Permission.objects.filter(
                    Q(content_type__app_label="festivals")
                    | Q(
                        content_type__app_label="applications",
                        content_type__model__in=[
                            "formfield", "formoption", "serviceoption"
                        ],
                    )
                )
            )
            admin.groups.add(group)

            participants = []
            for email, first_name, last_name in [
                ("user0@example.com", "Алексей", "Смирнов"),
                ("user1@example.com", "Мария", "Иванова"),
                ("user2@example.com", "Дмитрий", "Кузнецов"),
            ]:
                user, _ = User.objects.get_or_create(email=email)
                user.first_name, user.last_name = first_name, last_name
                user.is_staff = user.is_superuser = False
                user.is_active = True
                user.set_password(
                    "Simple_Password#1" if email == "user0@example.com" else "Demo-RiFest-2027!"
                )
                user.save()
                participants.append(user)

            start = date(2027, 1, 28)
            today = timezone.localdate()
            specs = [
                ("РИ-фест 2027: Первый ролевой", "open", start, date(2027, 1, 31)),
                ("Северный ветер", "open", today + timedelta(days=60), today + timedelta(days=63)),
                ("Мастерская миров", "draft", today + timedelta(days=90), today + timedelta(days=92)),
                ("Осенние встречи", "archived", today - timedelta(days=23), today - timedelta(days=20)),
            ]

            for festival_index, (name, status, start_date, end_date) in enumerate(specs):
                festival = Festival.objects.create(
                    name=name,
                    theme="22-й Сибирский фестиваль ролевых игр. Ролевые игры, общение и совместное творчество.",
                    status=status,
                    start_date=start_date,
                    end_date=end_date,
                    published_at=None if status == "draft" else timezone.now(),
                )
                fields = {}

                def field(key, field_type, label, **kwargs):
                    fields[key] = FormField.objects.create(
                        festival=festival,
                        field_type=field_type,
                        label=label,
                        order=len(fields),
                        **kwargs,
                    )
                    return fields[key]

                field("welcome", "info", "РИ-фест — Первый ролевой", help_text="Заезд 28 января с 15:00, открытие 29 января около 14:00. Фестиваль завершится 31 января после обеда. Мероприятие 18+; несовершеннолетние допускаются только по индивидуальному согласованию.")
                field("name", "text", "Имя или игровой псевдоним", is_required=True)
                field("about", "textarea", "Расскажите о себе и своих интересах")
                field("age", "number", "Возраст", is_required=True)
                field("arrival", "date", "Дата приезда")
                field("rules", "boolean", "Подтверждаю, что ознакомился(-ась) с правилами", is_required=True)
                single = field("transport", "single", "Как вы доберётесь до фестиваля?")
                single_options = [
                    FormOption.objects.create(field=single, label=label, order=i)
                    for i, label in enumerate(["Автобус №12 до остановки «Школа глухих»", "На автомобиле через КПП №1", "Другой способ"])
                ]
                multi = field("interests", "multi", "В каких активностях хотите участвовать?")
                multi_options = [
                    FormOption.objects.create(field=multi, label=label, order=i)
                    for i, label in enumerate(["Ролевые игры", "Мастерские", "Настольные игры", "Готов провести свою активность"])
                ]
                field("hidden", "text", "Внутренняя заметка организатора", is_active=False)

                lodging = field("lodging", "service1", "Проживание — 2 070 ₽ за сутки")
                lodging_options = [
                    ServiceOption.objects.create(field=lodging, label="Ночь 28–29 января", service_date=start, price=Decimal("2070.00")),
                    ServiceOption.objects.create(field=lodging, label="Ночь 29–30 января", service_date=date(2027, 1, 29), price=Decimal("2070.00"), order=1),
                    ServiceOption.objects.create(field=lodging, label="Ночь 30–31 января", service_date=date(2027, 1, 30), price=Decimal("2070.00"), order=2),
                    ServiceOption.objects.create(field=lodging, label="Без проживания", is_opt_out=True, order=3),
                ]
                meals = field("meals", "service", "Питание по датам и приёмам пищи")
                meal_options = []
                for i, (day, label, price) in enumerate([
                    (date(2027, 1, 29), "Пятница, завтрак", "250.00"),
                    (date(2027, 1, 29), "Пятница, обед", "450.00"),
                    (date(2027, 1, 29), "Пятница, ужин", "400.00"),
                    (date(2027, 1, 30), "Суббота, завтрак", "250.00"),
                    (date(2027, 1, 30), "Суббота, обед", "450.00"),
                    (date(2027, 1, 30), "Суббота, ужин", "400.00"),
                ]):
                    meal_options.append(ServiceOption.objects.create(field=meals, label=label, service_date=day, price=Decimal(price), order=i))
                meal_opt_out = ServiceOption.objects.create(
                    field=meals,
                    label="Питаюсь самостоятельно",
                    is_opt_out=True,
                    order=len(meal_options),
                )

                fee = field("fee", "datefee", "Организационный взнос")
                for ends_on, amount in [
                    (date(2026, 11, 30), "1300.00"),
                    (date(2027, 1, 10), "1400.00"),
                    (date(2027, 1, 27), "1600.00"),
                    (None, "1800.00"),
                ]:
                    FeePeriod.objects.create(field=fee, ends_on=ends_on, amount=Decimal(amount))

                if status == "draft":
                    continue
                owners = [admin, *participants]
                for person_index, owner in enumerate(owners):
                    app_status = (
                        "archived" if status == "archived"
                        else "withdrawn" if person_index == 1 and festival_index == 1
                        else "active"
                    )
                    app = Application.objects.create(owner=owner, festival=festival, status=app_status)
                    if person_index == 2:
                        Application.objects.filter(pk=app.pk).update(status="draft")
                        continue
                    answers = {
                        "name": f"{owner.first_name} {owner.last_name}",
                        "about": "Приезжаю за общением, играми и совместным творчеством.",
                        "age": 29 + person_index,
                        "arrival": start_date.isoformat(),
                        "rules": True,
                        "transport": str(single_options[person_index % len(single_options)].pk),
                        "interests": [str(item.pk) for item in multi_options[: person_index % 3 + 1]],
                        "hidden": "Участвовал(-а) раньше" if person_index == 0 else "Новый участник",
                    }
                    ApplicationAnswer.objects.bulk_create([
                        ApplicationAnswer(application=app, field=fields[key], value=value)
                        for key, value in answers.items()
                    ])
                    selected = [lodging_options[person_index % 3]]
                    selected += (
                        [meal_opt_out]
                        if person_index == 3
                        else meal_options[person_index::3][:2]
                    )
                    ApplicationServiceSelection.objects.bulk_create([
                        ApplicationServiceSelection(application=app, option=option)
                        for option in selected
                    ])

                self.stdout.write(f"Создан фестиваль и анкеты: {name}")

        self.stdout.write(self.style.SUCCESS("Демо-данные заменены."))
