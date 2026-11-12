document.addEventListener("DOMContentLoaded", () => {
    if (document.body.matches(".app-applications.model-application")) {
        const table = document.getElementById("result_list");
        const scroller = table?.parentElement;
        if (scroller) {
            const hint = document.createElement("p");
            hint.className = "admin-table-scroll-hint";
            hint.textContent = "Прокрутите таблицу вбок, чтобы увидеть все столбцы.";
            scroller.before(hint);
            const updateHint = () => {
                hint.hidden = scroller.scrollWidth <= scroller.clientWidth;
            };
            new ResizeObserver(updateHint).observe(scroller);
            updateHint();
        }
    }
    const toolbar = document.getElementById("toolbar");
    const filters = document.getElementById("changelist-filter");
    const row = toolbar?.parentElement;
    if (!row) return;
    row.classList.add("admin-list-toolbar");
    if (filters) {
        const originalParent = filters.parentElement;
        const compact = window.matchMedia("(max-width: 1023px)");
        const positionFilters = () => {
            if (compact.matches) row.after(filters);
            else originalParent.append(filters);
        };
        positionFilters();
        compact.addEventListener("change", positionFilters);
    }
});
