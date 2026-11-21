document.addEventListener("DOMContentLoaded", () => {
    const dialog = document.getElementById("application-export-dialog");
    const form = document.getElementById("application-export-form");
    if (!dialog || !form) return;
    const button = document.querySelector("[data-export-open]");
    if (!button) return;
    button.addEventListener("click", () => dialog.showModal());
    dialog.querySelector("[data-export-close]").addEventListener("click", () => dialog.close());
    form.addEventListener("submit", event => {
        event.preventDefault();
        const mode = form.elements.mode.value;
        const root = document.querySelector(".answer-table");
        const url = new URL(location.href);
        url.searchParams.delete("p");
        url.searchParams.delete("column");
        url.searchParams.set("export", mode);
        url.searchParams.delete("column_state");
        if (root) {
            const checked = [...document.querySelectorAll("#answer-columns [data-column-toggle]:checked")];
            const error = document.getElementById("application-export-error");
            error.hidden = mode !== "filtered" || checked.length > 0;
            if (!error.hidden) return;
            if (mode === "all") url.searchParams.set("column_state", "1");
            checked.forEach(input => {
                const id = input.dataset.columnToggle;
                url.searchParams.append("column", id.startsWith("fixed-") ? id.slice(6) : `field_${id}`);
            });
        }
        dialog.close();
        window.location.assign(url.href);
    });
});
