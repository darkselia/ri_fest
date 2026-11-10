from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from billing.calculator import calc_application_total
from festivals.models import Festival
from festivals.services import get_effective_status

from .forms import TemplatePreviewForm, display_date
from .models import Application
from .schema import export_schema, schema_revision
from .services import (
    application_form,
    application_form_context,
    delete_application,
    save_application,
    schema_errors,
    withdraw_application,
)


def available_festivals():
    return [
        festival
        for festival in Festival.objects.filter(status__in=["open", "running"])
        .prefetch_related(
            "form_fields__options",
            "form_fields__service_options",
            "form_fields__fee_periods",
        )
        .order_by("start_date", "pk")
        if get_effective_status(festival) == Festival.Status.OPEN
        and not schema_errors(festival)
    ]


def application_context(application):
    festival = application.festival
    state = get_effective_status(festival)
    status = application.status
    if state == Festival.Status.ARCHIVED and status == Application.Status.ACTIVE:
        status = Application.Status.ARCHIVED
    archived = status in (Application.Status.ARCHIVED, Application.Status.WITHDRAWN)
    draft = status == Application.Status.DRAFT
    reason = {
        "draft": "Приём анкет сейчас закрыт.",
        "running": "Фестиваль уже начался. Анкета доступна для просмотра. По вопросам участия и изменения данных обратитесь к организаторам.",
        "archived": "Фестиваль завершился. Сохранённый черновик доступен для просмотра и удаления."
        if draft
        else "Фестиваль завершился, анкета перенесена в архив. Здесь сохранены ваши ответы и выбранные услуги.",
    }.get(state, "")
    if status == Application.Status.WITHDRAWN:
        reason = "Участие по этой анкете отменено. Ответы и выбранные услуги сохранены в архиве."
        if state in ("open", "running"):
            reason += " Для восстановления участия обратитесь к организаторам."
    editable = draft or application.status == Application.Status.ACTIVE
    if state == "open" and editable and schema_errors(festival):
        reason = "Форма фестиваля временно недоступна для заполнения."
    return {
        "application": application,
        "festival": festival,
        "is_draft": draft,
        "archived": archived,
        "status_label": Application.Status(status).label,
        "can_edit": editable and state == "open" and not reason,
        "can_withdraw": status == Application.Status.ACTIVE and state == "open",
        "closed_reason": reason,
    }


def dashboard_context(user, params):
    tab = params.get("tab", "all")
    if tab not in ("all", "drafts", "submitted", "archive"):
        tab = "all"
    festivals = {
        festival.pk: festival
        for festival in Festival.objects.filter(applications__owner=user)
        .distinct()
        .prefetch_related(
            "form_fields__options",
            "form_fields__service_options",
            "form_fields__fee_periods",
        )
    }
    archived_ids = [
        pk
        for pk, festival in festivals.items()
        if get_effective_status(festival) == "archived"
    ]
    query = Application.objects.for_owner(user)
    archive_filter = Q(status__in=["archived", "withdrawn"]) | Q(
        status="active", festival_id__in=archived_ids
    )
    submitted_filter = Q(status="active") & ~Q(festival_id__in=archived_ids)
    counts = query.aggregate(
        all=Count("pk"),
        drafts=Count("pk", filter=Q(status="draft")),
        submitted=Count("pk", filter=submitted_filter),
        archive=Count("pk", filter=archive_filter),
    )
    if tab == "archive":
        query = query.filter(archive_filter)
    elif tab != "all":
        query = (
            query.filter(status="draft")
            if tab == "drafts"
            else query.filter(submitted_filter)
        )
    query = query.prefetch_related("answers", "service_selections__option", "fee_locks")
    page = Paginator(query, 20).get_page(params.get("page"))
    cards = []
    for application in page:
        application.festival = festivals[application.festival_id]
        card = application_context(application)
        card["needs_attention"] = False
        if application.status == "active" and not card["archived"]:
            saved = application_form(application.festival, application)
            checked = application_form(
                application.festival,
                application,
                data=saved.initial,
                require_complete=True,
            )
            card["needs_attention"] = not checked.is_valid()
            if card["needs_attention"]:
                card["attention_message"] = (
                    "Форма анкеты изменилась. Дополните или исправьте ответы."
                    if card["can_edit"]
                    else "Форма анкеты изменилась. Для дополнения ответов обратитесь к организаторам."
                )
        cards.append(card)
    return {"tab": tab, "page_obj": page, "cards": cards, "application_counts": counts}


def own_application(user, pk):
    return get_object_or_404(
        Application.objects.for_owner(user).select_related("festival"), pk=pk
    )


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def edit_application(request, pk=None):
    application = own_application(request.user, pk) if pk else None
    festivals = [] if application else available_festivals()
    festival = application.festival if application else None
    snapshot = None
    if request.method == "POST" and not application:
        try:
            candidate = signing.loads(
                request.POST.get("form_snapshot", ""),
                salt="applications.form",
                max_age=3600,
            )
            if candidate["actor"] == request.user.pk and str(
                candidate["festival"]
            ) == request.POST.get("festival"):
                snapshot = candidate
        except (signing.BadSignature, KeyError, TypeError):
            pass
    if festival is None:
        selected = (
            request.POST.get("festival")
            if request.method == "POST"
            else request.GET.get("festival")
        )
        if not selected and len(festivals) == 1:
            selected = str(festivals[0].pk)
        festival = next((item for item in festivals if str(item.pk) == selected), None)
        if (
            festival is None
            and request.method == "POST"
            and selected
            and selected.isdecimal()
        ):
            festival = Festival.objects.filter(
                pk=int(selected), status__in=["open", "running", "archived"]
            ).first()
            if snapshot is not None:
                festival = Festival.objects.filter(pk=int(selected)).first()
    context = {
        "account_tab": "applications",
        "application": application,
        "festivals": festivals,
        "festival": festival,
    }
    if festival is None:
        if request.method == "POST":
            context["error"] = (
                "Выбранный фестиваль недоступен. Выберите фестиваль с открытым приёмом."
            )
        return render(
            request,
            "applications/edit.html",
            context,
            status=409 if request.method == "POST" else 200,
        )
    state = application_context(application) if application else {"can_edit": True}
    if application:
        context.update(state)
    if request.method == "GET" and application and not state["can_edit"]:
        return redirect("applications:detail", pk=pk)
    form = application_form(festival, application)
    status = 200
    if request.method == "POST":
        data = request.POST.copy()
        action = data.pop("action", [""])[0]
        revision = data.pop("schema_revision", [""])[0]
        data.pop("form_snapshot", None)
        posted_festival = data.pop("festival", [str(festival.pk)])[0]
        try:
            allowed_actions = (
                {"update"}
                if application and application.status == "active"
                else {"draft", "submit"}
            )
            if action not in allowed_actions or posted_festival != str(festival.pk):
                raise ValidationError("Недопустимое действие или фестиваль.")
            application, form = save_application(
                actor=request.user,
                data=data,
                application_id=pk,
                festival_id=None if pk else festival.pk,
                submit=action == "submit",
                revision=revision,
            )
            if form.is_valid():
                messages.success(
                    request,
                    "Изменения анкеты сохранены."
                    if action == "update"
                    else "Анкета отправлена. Участие зарегистрировано."
                    if action == "submit"
                    else "Черновик сохранён. Он ещё не отправлен.",
                )
                if action == "submit":
                    return redirect("accounts:home")
                return redirect("applications:edit", pk=application.pk)
            if form.non_field_errors():
                status = 409
        except (PermissionDenied, ValidationError) as error:
            form = (
                TemplatePreviewForm(
                    snapshot["rows"],
                    data=data,
                    preview=False,
                    require_complete=False,
                    timezone_name=festival.timezone,
                )
                if snapshot is not None and get_effective_status(festival) != "open"
                else application_form(festival, application, data=data)
            )
            form.is_valid()
            form.add_error(
                None,
                " ".join(error.messages)
                if isinstance(error, ValidationError)
                else str(error) or "Действие больше недоступно.",
            )
            status = 409
        except (Application.DoesNotExist, Festival.DoesNotExist):
            raise Http404 from None
    context.update(application_form_context(form, application))
    context.update(
        form=form,
        preview_form=form,
        schema_revision=schema_revision(festival),
    )
    displayed_revision = (
        snapshot["revision"]
        if snapshot is not None and get_effective_status(festival) != "open"
        else schema_revision(festival)
    )
    context["schema_revision"] = displayed_revision
    context["form_snapshot"] = signing.dumps(
        {
            "actor": request.user.pk,
            "festival": festival.pk,
            "rows": [entry["row"] for entry in form.entries],
            "revision": displayed_revision,
        },
        salt="applications.form",
        compress=True,
    )
    return render(request, "applications/edit.html", context, status=status)


@never_cache
@login_required
@require_http_methods(["GET"])
def detail(request, pk):
    application = own_application(request.user, pk)
    context = {"account_tab": "applications", **application_context(application)}
    answers = {answer.field_id: answer.value for answer in application.answers.all()}
    entries = []
    for row in export_schema(application.festival):
        if not row["is_active"] or row["field_type"] in (
            "info",
            "service",
            "service1",
            "datefee",
        ):
            continue
        value = answers.get(row["id"])
        if row["field_type"] in ("single", "multi"):
            values = value if isinstance(value, list) else [value]
            labels = {
                str(option["id"]): option["label"]
                + (" — больше недоступен" if not option["is_active"] else "")
                for option in row["options"]
            }
            value = ", ".join(
                labels.get(str(item), "Вариант недоступен")
                for item in values
                if item is not None
            )
        elif row["field_type"] == "boolean" and value is not None:
            value = "Да" if value else "Нет"
        elif row["field_type"] == "date" and value:
            value = display_date(value)
        entries.append(
            {
                "label": row["label"],
                "value": value if value not in (None, "") else "Не заполнено",
            }
        )
    fee_entries = (
        []
        if get_effective_status(application.festival) == "archived"
        else [
            entry
            for entry in application_form(application.festival, application).entries
            if entry["kind"] == "datefee"
        ]
    )
    totals = calc_application_total(application)
    context.update(
        entries=entries,
        fee_entries=fee_entries,
        selections=application.service_selections.select_related("option__field"),
        totals=totals,
        financial_totals=totals,
    )
    return render(request, "applications/detail.html", context)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def delete(request, pk):
    application = own_application(request.user, pk)
    context = {"account_tab": "applications", **application_context(application)}
    if not context["is_draft"]:
        raise PermissionDenied("Удалить можно только черновик.")
    if request.method == "POST":
        try:
            delete_application(pk, actor=request.user)
        except (Application.DoesNotExist, Festival.DoesNotExist):
            raise Http404 from None
        except PermissionDenied as error:
            context["error"] = str(error)
            return render(request, "applications/delete.html", context, status=409)
        messages.success(request, "Анкета удалена.")
        return redirect("/account/?tab=drafts")
    return render(request, "applications/delete.html", context)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def withdraw(request, pk):
    application = own_application(request.user, pk)
    context = {"account_tab": "applications", **application_context(application)}
    if not context["can_withdraw"]:
        raise PermissionDenied(
            "Отозвать можно только активную анкету при открытом приёме."
        )
    if request.method == "POST":
        try:
            token = signing.loads(
                request.POST.get("confirmation", ""),
                salt="applications.withdraw",
                max_age=3600,
            )
            if token["actor"] != request.user.pk or token["application"] != pk:
                raise signing.BadSignature
            withdraw_application(
                pk, actor=request.user, expected_updated_at=token["updated_at"]
            )
        except (Application.DoesNotExist, Festival.DoesNotExist):
            raise Http404 from None
        except (signing.BadSignature, KeyError, TypeError, ValidationError):
            context["error"] = (
                "Анкета или подтверждение изменились. Проверьте сведения и подтвердите отзыв заново."
            )
        except PermissionDenied as error:
            context["error"] = str(error)
        else:
            messages.success(request, "Анкета отозвана и перенесена в архив.")
            return redirect("applications:detail", pk=pk)
    application.refresh_from_db()
    context["confirmation"] = signing.dumps(
        {
            "actor": request.user.pk,
            "application": pk,
            "updated_at": application.updated_at.isoformat(),
        },
        salt="applications.withdraw",
    )
    return render(
        request,
        "applications/withdraw.html",
        context,
        status=409 if context.get("error") else 200,
    )
