document.addEventListener("DOMContentLoaded", () => {
    const root = document.querySelector(".answer-table");
    if (!root) return;
    const key = `answer-columns:${root.dataset.festival}`;
    const columns = [...root.querySelectorAll("[data-column-toggle]")];
    const measure = document.createElement("canvas").getContext("2d");
    columns.forEach(input => {
        const header = root.querySelector(`th.answer-column-${input.dataset.columnToggle}`);
        if (!header || !measure) return;
        measure.font = getComputedStyle(header).font;
        const words = input.parentElement.textContent.trim().split(/\s+/);
        const longest = Math.max(...words.map(word => measure.measureText(word).width));
        const width = Math.max(Number(input.dataset.columnWidth), Math.ceil(longest) + 32);
        input.dataset.columnWidth = String(width);
        header.style.width = `${width}px`;
        header.style.minWidth = `${width}px`;
    });
    let saved = null;
    try {
        saved = JSON.parse(sessionStorage.getItem(key));
    } catch {
        saved = null;
    }
    if (Array.isArray(saved)) columns.forEach(input => {
        input.checked = saved.includes(input.dataset.columnToggle);
    });
    const update = () => {
        const visible = columns.filter(input => input.checked).map(input => input.dataset.columnToggle);
        const results = root.querySelector(".answer-table__results");
        if (results) {
            const width = columns.filter(input => input.checked).reduce((sum, input) => sum + Number(input.dataset.columnWidth), Number(results.dataset.baseWidth));
            results.style.setProperty("--answer-table-width", `${width}px`);
        }
        columns.forEach(input => {
            root.querySelectorAll(`.answer-column-${input.dataset.columnToggle}`).forEach(cell => {
                cell.hidden = !visible.includes(input.dataset.columnToggle);
            });
        });
        try {
            sessionStorage.setItem(key, JSON.stringify(visible));
        } catch {
            /* Storage can be disabled. */
        }
    };
    columns.forEach(input => input.addEventListener("change", update));
    update();
    root.querySelectorAll("[data-submit]").forEach(input => input.addEventListener("change", () => {
        if (!input.classList.contains("select2-hidden-accessible")) input.form.requestSubmit();
    }));
    if (window.django?.jQuery) {
        window.django.jQuery(root).on("change", "select[data-submit].select2-hidden-accessible", function() {
            this.form.requestSubmit();
        });
    }
    const panels = [...root.querySelectorAll("[data-panel]")];
    const compact = matchMedia("(width < 1024px)");
    const sidebar = root.querySelector(".answer-table__sidebar");
    const toolbar = root.querySelector(".answer-table__toolbar");
    const layout = root.querySelector(".answer-table__layout");
    panels.forEach(button => {
        try {
            button.setAttribute("aria-expanded", sessionStorage.getItem(`answer-panel:${button.dataset.panel}`) === "open" ? "true" : "false");
        } catch {
            /* Storage can be disabled. */
        }
    });
    const syncPanels = () => {
        if (sidebar) {
            if (compact.matches) toolbar.after(sidebar);
            else layout.append(sidebar);
        }
        panels.forEach(button => {
            const panel = document.getElementById(button.dataset.panel);
            if (compact.matches) panel.toggleAttribute("data-collapsed", button.getAttribute("aria-expanded") !== "true");
            else panel.removeAttribute("data-collapsed");
        });
    };
    panels.forEach(button => button.addEventListener("click", () => {
        button.setAttribute("aria-expanded", String(button.getAttribute("aria-expanded") !== "true"));
        try {
            sessionStorage.setItem(`answer-panel:${button.dataset.panel}`, button.getAttribute("aria-expanded") === "true" ? "open" : "closed");
        } catch {
            /* Storage can be disabled. */
        }
        syncPanels();
    }));
    compact.addEventListener("change", syncPanels);
    syncPanels();
});
