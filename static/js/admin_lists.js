document.addEventListener("DOMContentLoaded", () => {
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
