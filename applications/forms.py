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


class SchemaEditorWidgets(forms.Form):
    text = forms.CharField(widget=UnfoldAdminTextInputWidget)
    textarea = forms.CharField(widget=UnfoldAdminTextareaWidget(attrs={"rows": 2}))
    select = forms.CharField(widget=UnfoldAdminSelectWidget)
    number = forms.DecimalField(widget=UnfoldAdminDecimalFieldWidget)
    date = forms.DateField(widget=NativeDateInput)
    checkbox = forms.BooleanField(widget=UnfoldBooleanWidget)


class PriceCheckboxes(UnfoldAdminCheckboxSelectMultipleWidget):
    def __init__(self, prices, *args, **kwargs):
        self.prices = prices
        super().__init__(*args, **kwargs)

    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex, attrs
        )
        price = self.prices[str(value)]
        option["attrs"]["data-kopecks"] = str(price)
        return option


class TemplatePreviewForm(forms.Form):
    def __init__(
        self, rows, data=None, *, answers=None, require_complete=True, preview=True
    ):
        super().__init__(
            data=data,
            prefix="preview" if preview else None,
            use_required_attribute=False,
        )
        self.entries = []
        self.stale = {}
        answers = answers or {}
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
                    from decimal import Decimal

                    for j, option in enumerate(row["services"]):
                        if not option["is_active"]:
                            continue
                        key = str(option["id"]) if option.get("id") else f"new_{j}"
                        date = (
                            f" — {option['service_date']}"
                            if option.get("service_date")
                            else ""
                        )
                        choices.append(
                            (key, f"{option['label']}{date} ({option['price']} ₽)")
                        )
                        prices[key] = int(Decimal(option["price"]) * 100)
                    field = forms.MultipleChoiceField(
                        choices=choices, widget=PriceCheckboxes(prices), **settings
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
                            widget=UnfoldAdminCheckboxSelectMultipleWidget,
                            **settings,
                        )
                    )
                elif kind == "number":
                    field = forms.DecimalField(**settings)
                elif kind == "date":
                    field = forms.DateField(
                        widget=NativeDateInput
                        if preview
                        else forms.DateInput(attrs={"type": "date"}),
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
                if preview and kind in (
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
                if row.get("id") in answers:
                    self.initial[name] = answers[row["id"]]
                entry["field"] = self[name]
            self.entries.append(entry)

        self.require_complete = require_complete

    def clean(self):
        cleaned = super().clean()
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
            entry["key"]: self.cleaned_data.get(entry["field"].name, [])
            for entry in self.entries
            if entry["kind"] in PRICE_TYPES
        }
