from datetime import timedelta
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
    FormField,
    FormOption,
    ServiceOption,
)
from festivals.models import Festival


class Command(BaseCommand):
    help = "Создать постоянные вымышленные данные для проверки интерфейса. Повторный запуск сохраняет ручные правки."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true")
        mode.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        self.stdout.write(
            "Демо-набор: два открытых фестиваля, черновик, проходящий и архивный; два участника и организатор; анкеты всех статусов и услуги обоих типов."
        )
        if not options["apply"]:
            self.stdout.write(
                "Предварительный просмотр. Для создания выполните seed_demo_data --apply. Существующие записи не перезаписываются."
            )
            return
        with transaction.atomic():
            users = []
            for email, first, last, staff in [
                ("demo.anna@example.invalid", "Анна", "Лескова", False),
                ("demo.ilya@example.invalid", "Илья", "Соколов", False),
                ("demo.organizer@example.invalid", "Мария", "Орлова", True),
            ]:
                user = User.objects.filter(email=email).first()
                if user is None:
                    user = User.objects.create_user(
                        email,
                        "Demo-RiFest-2026!",
                        first_name=first,
                        last_name=last,
                        is_staff=staff,
                    )
                    if staff:
                        group, created = Group.objects.get_or_create(
                            name="Организаторы"
                        )
                        if created:
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
                        user.groups.add(group)
                    self.stdout.write(f"Создан: {email}; пароль: Demo-RiFest-2026!")
                else:
                    self.stdout.write(
                        f"Сохранён существующий аккаунт: {email}; пароль и права не менялись."
                    )
                users.append(user)
            now = timezone.now()
            today = timezone.localdate()
            specs = [
                ("[Демо] Лесные истории", "open", 30, 33),
                ("[Демо] Северный ветер", "open", 60, 63),
                ("[Демо] Мастерская миров", "draft", 90, 92),
                ("[Демо] Город легенд", "running", -1, 2),
                ("[Демо] Осенние встречи", "archived", -23, -20),
            ]
            for index, (name, status, start, end) in enumerate(specs):
                if Festival.objects.filter(name=name).exists():
                    self.stdout.write(f"Существующий набор не изменён: {name}")
                    continue
                festival = Festival.objects.create(
                    name=name,
                    theme="Вымышленные данные для проверки кабинета и редактора",
                    status=status,
                    start_date=today + timedelta(days=start),
                    end_date=today + timedelta(days=end),
                    published_at=None if status == "draft" else now,
                )
                person = FormField.objects.create(
                    festival=festival,
                    field_type="text",
                    label="Имя или игровой псевдоним",
                    is_required=True,
                )
                FormField.objects.create(
                    festival=festival,
                    field_type="info",
                    label="Добро пожаловать",
                    help_text="Это демонстрационная анкета. Все имена и данные вымышлены.",
                    order=1,
                )
                activity = FormField.objects.create(
                    festival=festival,
                    field_type="multi",
                    label="Что вам интересно?",
                    order=2,
                )
                old = FormOption.objects.create(
                    field=activity, label="Мастерские", is_active=False
                )
                FormOption.objects.create(
                    field=activity, label="Настольные игры", order=1
                )
                FormOption.objects.create(
                    field=activity, label="Живые ролевые игры", order=2
                )
                FormField.objects.create(
                    festival=festival,
                    field_type="textarea",
                    label="Пожелания организаторам",
                    order=3,
                )
                FormField.objects.create(
                    festival=festival,
                    field_type="boolean",
                    label="Нужна помощь с проездом",
                    order=4,
                )
                hidden = FormField.objects.create(
                    festival=festival,
                    field_type="text",
                    label="Предыдущий опыт участия",
                    is_active=False,
                    order=5,
                )
                meal = FormField.objects.create(
                    festival=festival,
                    field_type="service",
                    label="Питание",
                    is_required=True,
                    order=6,
                )
                lunch = ServiceOption.objects.create(
                    field=meal,
                    label="Обед в субботу",
                    service_date=festival.start_date,
                    price=Decimal("450.00"),
                    is_active=False,
                )
                ServiceOption.objects.create(
                    field=meal,
                    label="Ужин в субботу",
                    service_date=festival.start_date,
                    price=Decimal("600.00"),
                    order=1,
                )
                ServiceOption.objects.create(
                    field=meal, label="Чай в общей зоне", price=0, order=2
                )
                no_meal = ServiceOption.objects.create(
                    field=meal,
                    label="Питаюсь самостоятельно",
                    price=0,
                    is_opt_out=True,
                    order=3,
                )
                stay = FormField.objects.create(
                    festival=festival,
                    field_type="service1",
                    label="Размещение",
                    is_required=True,
                    order=7,
                )
                house = ServiceOption.objects.create(
                    field=stay, label="Место в доме", price=Decimal("3000.00")
                )
                ServiceOption.objects.create(
                    field=stay, label="Своя палатка", price=0, order=1
                )
                no_stay = ServiceOption.objects.create(
                    field=stay,
                    label="Без проживания",
                    price=0,
                    is_opt_out=True,
                    order=2,
                )
                if status == "draft":
                    self.stdout.write(f"Создан: {name}")
                    continue
                records = [
                    (
                        users[0],
                        "archived"
                        if status == "archived"
                        else "withdrawn"
                        if index == 1
                        else "active",
                    ),
                    (users[1], "archived" if status == "archived" else "active"),
                ]
                if index == 0:
                    records.append((users[0], "draft"))
                    records.append((users[0], "withdrawn"))
                if status == "archived":
                    records.append((users[0], "draft"))
                for number, (owner, app_status) in enumerate(records):
                    app = Application.objects.create(
                        owner=owner, festival=festival, status=app_status
                    )
                    if number == 2:
                        continue
                    ApplicationAnswer.objects.create(
                        application=app,
                        field=person,
                        value=f"{owner.first_name} {owner.last_name}",
                    )
                    ApplicationAnswer.objects.create(
                        application=app,
                        field=activity,
                        value=[str(old.pk)] if number == 0 else [],
                    )
                    ApplicationAnswer.objects.create(
                        application=app, field=hidden, value="Участвую второй раз"
                    )
                    for option in [lunch, house] if number == 0 else [no_meal, no_stay]:
                        ApplicationServiceSelection.objects.create(
                            application=app, option=option
                        )
                self.stdout.write(f"Создан: {name}")
        self.stdout.write(
            self.style.SUCCESS(
                "Демонстрационные данные сохранены. Они не будут автоматически удалены."
            )
        )
