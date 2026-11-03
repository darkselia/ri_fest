from datetime import date

from django import forms
from unfold.widgets import (
    UnfoldAdminCheckboxSelectMultipleWidget,
    UnfoldAdminDecimalFieldWidget,
    UnfoldAdminSelectWidget,
    UnfoldAdminTextareaWidget,
    UnfoldAdminTextInputWidget,
    UnfoldBooleanWidget,
)

from .schema import PRICE_TYPES


class NativeDateInput(UnfoldAdminTextInputWidget):
    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs)
        self.format = "%Y-%m-%d"

    def format_value(self, value):
        return value.isoformat() if hasattr(value, "isoformat") else value


def display_date(value):
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return value
    return f"{value.day:02d}.{value.month:02d}.{value.year:04d}" if value else ""


class PublicDateInput(forms.DateInput):
    def __init__(self):
        super().__init__(
            format="%d.%m.%Y",
            attrs={
                "placeholder": "дд.мм.гггг",
                "maxlength": 10,
                "pattern": r"\d{2}\.\d{2}\.\d{4}",
            },
        )

    def format_value(self, value):
        return display_date(value)


class SchemaEditorWidgets(forms.Form):
    text = forms.CharField(widget=UnfoldAdminTextInputWidget)
    textarea = forms.CharField(widget=UnfoldAdminTextareaWidget(attrs={"rows": 2}))
    select = forms.CharField(widget=UnfoldAdminSelectWidget)
    number = forms.DecimalField(widget=UnfoldAdminDecimalFieldWidget)
    date = forms.DateField(widget=NativeDateInput)
    checkbox = forms.BooleanField(widget=UnfoldBooleanWidget)


class PriceCheckboxesMixin:
    def __init__(self, prices, *args, refusals=(), **kwargs):
        self.prices = prices
        self.refusals = set(refusals)
        super().__init__(*args, **kwargs)

    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex, attrs
        )
        price = self.prices[str(value)]
        option["attrs"]["data-kopecks"] = str(price)
        if str(value) in self.refusals:
            option["attrs"]["data-opt-out"] = "true"
        return option


class PriceCheckboxes(PriceCheckboxesMixin, UnfoldAdminCheckboxSelectMultipleWidget):
    pass


class PublicPriceCheckboxes(PriceCheckboxesMixin, forms.CheckboxSelectMultiple):
    pass


class PublicPriceRadios(PriceCheckboxesMixin, forms.RadioSelect):
    pass


class TemplatePreviewForm(forms.Form):
    def __init__(
        self,
        rows,
        data=None,
        *,
        answers=None,
        require_complete=True,
        preview=True,
        public=False,
    ):
        super().__init__(
            data=data,
            prefix="preview" if preview else None,
            use_required_attribute=False,
        )
        self.entries = []
        self.stale = {}
        answers = answers or {}
        use_admin = preview and not public
        for i, row in enumerate(rows):
            if not row["is_active"]:
                continue
            name = f"entry_{i}" if preview else f"field_{row['id']}"
            kind = row["field_type"]
            entry = {
                "row": row,
                "kind": kind,
                "key": str(row["id"]) if row.get("id") else f"new_{i}",
            }
            if kind != "info":
                settings = {
                    "label": row["label"],
                    "help_text": row["help_text"],
                    "required": row["is_required"] and require_complete,
                }
                if kind in PRICE_TYPES:
                    choices = []
                    prices = {}
                    refusals = []
                    from decimal import Decimal

                    for j, option in enumerate(row["services"]):
                        saved = answers.get(row.get("id"), [])
                        if (
                            not option["is_active"]
                            and str(option.get("id")) not in saved
                        ):
                            continue
                        key = str(option["id"]) if option.get("id") else f"new_{j}"
                        date = (
                            f" — {display_date(option['service_date'])}"
                            if option.get("service_date")
                            else ""
                        )
                        choices.append(
                            (
                                key,
                                f"{option['label']}{date} ({option['price']} ₽)"
                                + (
                                    " — сохранённая скрытая услуга"
                                    if not option["is_active"]
                                    else ""
                                ),
                            )
                        )
                        prices[key] = int(Decimal(option["price"]) * 100)
                        if option.get("is_opt_out"):
                            refusals.append(key)
                    single_service = kind == "service1"
                    field_class = (
                        forms.ChoiceField
                        if single_service
                        else forms.MultipleChoiceField
                    )
                    widget_class = (
                        PublicPriceRadios
                        if single_service
                        else (PriceCheckboxes if use_admin else PublicPriceCheckboxes)
                    )
                    field = field_class(
                        choices=choices,
                        widget=widget_class(prices, refusals=refusals),
                        **settings,
                    )
                elif kind in ("single", "multi"):
                    choices = [
                        (str(o["id"]) if o.get("id") else f"new_{j}", o["label"])
                        for j, o in enumerate(row["options"])
                        if o["is_active"]
                    ]
                    existing = answers.get(row.get("id"))
                    selected = (
                        existing
                        if isinstance(existing, list)
                        else ([existing] if existing else [])
                    )
                    inactive = {
                        str(o["id"]): o["label"]
                        for o in row["options"]
                        if not o["is_active"] and o.get("id")
                    }
                    old = [str(value) for value in selected if str(value) in inactive]
                    if old:
                        self.stale[name] = old
                        choices.extend(
                            (value, f"{inactive[value]} — больше недоступен")
                            for value in old
                        )
                    field = (
                        forms.ChoiceField(
                            choices=[("", "— Не выбрано —"), *choices], **settings
                        )
                        if kind == "single"
                        else forms.MultipleChoiceField(
                            choices=choices,
                            widget=UnfoldAdminCheckboxSelectMultipleWidget
                            if use_admin
                            else forms.CheckboxSelectMultiple,
                            **settings,
                        )
                    )
                elif kind == "number":
                    field = forms.DecimalField(**settings)
                elif kind == "date":
                    field = forms.DateField(
                        widget=NativeDateInput if use_admin else PublicDateInput(),
                        input_formats=["%d.%m.%Y", "%Y-%m-%d"],
                        error_messages={
                            "invalid": "Укажите существующую дату в формате дд.мм.гггг."
                        },
                        **settings,
                    )
                elif kind == "boolean":
                    field = forms.BooleanField(**settings)
                else:
                    field = forms.CharField(
                        widget=forms.Textarea(attrs={"rows": 3})
                        if kind == "textarea"
                        else forms.TextInput,
                        **settings,
                    )
                if use_admin and kind in (
                    "text",
                    "textarea",
                    "number",
                    "boolean",
                    "single",
                ):
                    widget = {
                        "text": UnfoldAdminTextInputWidget,
                        "textarea": UnfoldAdminTextareaWidget,
                        "number": UnfoldAdminDecimalFieldWidget,
                        "boolean": UnfoldBooleanWidget,
                        "single": UnfoldAdminSelectWidget,
                    }[kind]
                    field.widget = widget()
                    if kind == "single":
                        field.widget.choices = field.choices
                self.fields[name] = field
                if row["is_required"]:
                    field.widget.attrs["aria-required"] = "true"
                if row.get("id") in answers:
                    self.initial[name] = answers[row["id"]]
                    if kind == "service1":
                        self.initial[name] = next(iter(answers[row["id"]]), "")
                entry["field"] = self[name]
            self.entries.append(entry)

        self.require_complete = require_complete

    def clean(self):
        cleaned = super().clean()
        from .schema import validate_service_selection

        for entry in self.entries:
            if entry["kind"] not in PRICE_TYPES:
                continue
            name = entry["field"].name
            if (
                entry["kind"] == "service1"
                and hasattr(self.data, "getlist")
                and len(set(self.data.getlist(self.add_prefix(name)))) > 1
            ):
                self.add_error(name, "Выберите только один вариант услуги.")
                continue
            values = cleaned.get(name, [])
            if isinstance(values, str):
                values = [values] if values else []
            picked = [
                option
                for index, option in enumerate(entry["row"]["services"])
                if (str(option["id"]) if option.get("id") else f"new_{index}") in values
            ]
            try:
                validate_service_selection(entry["kind"], picked)
            except forms.ValidationError as error:
                self.add_error(name, error)
        if self.require_complete:
            for name, old in self.stale.items():
                value = cleaned.get(name)
                selected = (
                    value if isinstance(value, list) else ([value] if value else [])
                )
                if set(old) & set(selected):
                    self.add_error(
                        name, "Этот вариант больше недоступен. Выберите другой."
                    )
        return cleaned

    def service_selections(self):
        return {
            entry["key"]: (
                [self.cleaned_data[entry["field"].name]]
                if self.cleaned_data.get(entry["field"].name)
                else []
            )
            if entry["kind"] == "service1"
            else self.cleaned_data.get(entry["field"].name, [])
            for entry in self.entries
            if entry["kind"] in PRICE_TYPES
        }
