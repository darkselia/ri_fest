from django import forms

from .schema import PRICE_TYPES


class PriceCheckboxes(forms.CheckboxSelectMultiple):
    def __init__(self, prices, *args, **kwargs):
        self.prices = prices
        super().__init__(*args, **kwargs)

    def create_option(
        self, name, value, label, selected, index, subindex=None, attrs=None
    ):
        option = super().create_option(
            name, value, label, selected, index, subindex, attrs
        )
        price, opt_out = self.prices[str(value)]
        option["attrs"]["data-kopecks"] = str(price)
        option["attrs"]["data-opt-out"] = "true" if opt_out else "false"
        return option


class TemplatePreviewForm(forms.Form):
    def __init__(self, rows, data=None):
        super().__init__(data=data, prefix="preview", use_required_attribute=False)
        self.entries = []
        for i, row in enumerate(rows):
            if not row["is_active"]:
                continue
            name = f"entry_{i}"
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
                    "required": row["is_required"],
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
                        prices[key] = (
                            int(Decimal(option["price"]) * 100),
                            option["is_opt_out"],
                        )
                    field = forms.MultipleChoiceField(
                        choices=choices, widget=PriceCheckboxes(prices), **settings
                    )
                elif kind in ("single", "multi"):
                    choices = [
                        (str(o["id"]) if o.get("id") else f"new_{j}", o["label"])
                        for j, o in enumerate(row["options"])
                        if o["is_active"]
                    ]
                    field = (
                        forms.ChoiceField(
                            choices=[("", "— Не выбрано —"), *choices], **settings
                        )
                        if kind == "single"
                        else forms.MultipleChoiceField(
                            choices=choices,
                            widget=forms.CheckboxSelectMultiple,
                            **settings,
                        )
                    )
                elif kind == "number":
                    field = forms.DecimalField(**settings)
                elif kind == "date":
                    field = forms.DateField(
                        widget=forms.DateInput(attrs={"type": "date"}), **settings
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
                self.fields[name] = field
                entry["field"] = self[name]
            self.entries.append(entry)

    def service_selections(self):
        return {
            entry["key"]: self.cleaned_data.get(entry["field"].name, [])
            for entry in self.entries
            if entry["kind"] in PRICE_TYPES
        }
