document.querySelectorAll('select[name="balance"], select[name="application_status"]').forEach(select => {
    select.dataset.minimumResultsForSearch = "-1";
});

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("#filter-form details:has(input:checked)").forEach(details => {
        if (!details.closest(".application-filter-group")) details.open = true;
    });
    document.querySelectorAll(".application-filter-group .admin-filter-choice").forEach(details => {
        const summary = details.querySelector(":scope > summary");
        const name = document.createElement("span");
        name.className = "application-filter-summary-name";
        name.textContent = summary.textContent.trim();
        const value = document.createElement("span");
        value.className = "application-filter-summary-value";
        summary.replaceChildren(name, value);

        const updateSummary = () => {
            const selected = [...details.querySelectorAll("input:checked")];
            const selectedLink = details.querySelector("li.font-semibold a");
            if (selectedLink) selectedLink.setAttribute("aria-current", "true");
            const label = selected.length > 1 ?
                `Выбрано: ${selected.length}` :
                selected.length === 1 ?
                selected[0].closest("label").textContent.trim() :
                selectedLink?.textContent.trim() || "Все";
            const active = selected.length > 0 ?
                selected.some(input => input.value !== "") :
                Boolean(selectedLink && label !== "Все");
            value.textContent = label;
            value.title = label;
            value.classList.toggle("is-active", active);
        };
        updateSummary();
        details.addEventListener("change", updateSummary);
    });
    if (document.body.matches(".app-applications.model-application")) {
        const form = document.getElementById("filter-form");
        if (form) {
            const applyFilter = () => form.requestSubmit();
            if (window.django?.jQuery) {
                window.django.jQuery(form).on("change", "select, input[type=checkbox]", applyFilter);
            } else {
                form.addEventListener("change", applyFilter);
            }
        }
        const festival = document.querySelector('select[name="festival__id__exact"]');
        if (festival) {
            let resettingFestival = false;
            const resetFestivalFilters = event => {
                if (event.target !== festival) return;
                event.stopImmediatePropagation();
                event.stopPropagation();
                if (resettingFestival) return;
                resettingFestival = true;

                const current = new URL(window.location.href);
                const params = new URLSearchParams();
                for (const name of ["o", "ot"]) {
                    for (const value of current.searchParams.getAll(name)) {
                        params.append(name, value);
                    }
                }
                if (festival.value) {
                    params.set(festival.name, festival.value);
                }

                const query = params.toString();
                window.location.assign(`${current.pathname}${query ? `?${query}` : ""}`);
            };

            window.addEventListener("input", resetFestivalFilters, true);
            window.addEventListener("change", resetFestivalFilters, true);
            if (window.django?.jQuery) {
                window.django.jQuery(festival).on("input change", resetFestivalFilters);
            }
        }
    }
    const table = document.getElementById("result_list");
    const scroller = table?.parentElement;
    if (scroller) {
        const hint = document.createElement("p");
        hint.className = "admin-table-scroll-hint";
        hint.textContent = "Прокрутите таблицу вбок, чтобы увидеть все столбцы.";
        if (document.body.matches(".app-applications.model-application.change-list")) {
            hint.textContent += " Чтобы перейти в анкету нажмите на поле \"Участник\"";
        }
        const compact = matchMedia("(width < 1024px)");
        const positionHint = () => compact.matches ? scroller.after(hint) : scroller.before(hint);
        positionHint();
        compact.addEventListener("change", positionHint);
        const updateHint = () => {
            hint.hidden = scroller.scrollWidth <= scroller.clientWidth;
        };
        new ResizeObserver(updateHint).observe(scroller);
        updateHint();
    }
    const toolbar = document.getElementById("toolbar");
    const row = toolbar?.parentElement;
    if (row) row.classList.add("admin-list-toolbar");
});
