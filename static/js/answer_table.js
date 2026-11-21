document.addEventListener("DOMContentLoaded", () => {
    const root = document.querySelector(".answer-table");
    if (!root) return;
    const key = `answer-columns:${root.dataset.festival}`;
    const columns = [...root.querySelectorAll("[data-column-toggle]")];
    const measure = document.createElement("canvas").getContext("2d");
    const typeWidths = { text: 160, textarea: 200, number: 90, date: 120, boolean: 100, single: 150, multi: 180, service: 180, service1: 160 };
    const baseWidths = columns.map(input => typeWidths[input.dataset.fieldType] ?? Number(input.dataset.columnWidth));
    const measureWidths = () => {
        if (!measure) return;
        columns.forEach((input, index) => {
            const id = input.dataset.columnToggle;
            const header = root.querySelector(`th.answer-column-${id}`);
            if (!header) return;
            measure.font = getComputedStyle(header).font;
            const words = input.parentElement.textContent.trim().split(/\s+/);
            const longest = Math.max(...words.map(word => measure.measureText(word).width));
            let width = Math.max(baseWidths[index], Math.ceil(longest) + 32);
            if (input.dataset.fieldType === "number") {
                root.querySelectorAll(`td.answer-column-${id}`).forEach(cell => {
                    measure.font = getComputedStyle(cell).font;
                    width = Math.max(width, Math.ceil(measure.measureText(cell.textContent.trim()).width) + 24);
                });
            }
            if (id === "fixed-fees") {
                root.querySelectorAll(`td.answer-column-${id} .registry-fee`).forEach(fee => {
                    measure.font = getComputedStyle(fee).font;
                    const label = [...fee.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join("").trim();
                    width = Math.max(width, Math.min(220, Math.ceil(measure.measureText(label).width) + 48));
                });
            }
            input.dataset.columnWidth = String(width);
            header.style.width = `${width}px`;
            header.style.minWidth = `${width}px`;
        });
    };
    measureWidths();
    let saved = null;
    try {
        saved = JSON.parse(sessionStorage.getItem(key));
    } catch {
        saved = null;
    }
    const legacy = Array.isArray(saved);
    const savedColumns = legacy ? saved : saved?.visible;
    if (Array.isArray(savedColumns)) columns.forEach(input => {
        const id = input.dataset.columnToggle;
        input.checked = savedColumns.includes(id) || (legacy && id.startsWith("fixed-"));
    });
    const update = () => {
        const visible = columns.filter(input => input.checked).map(input => input.dataset.columnToggle);
        const results = root.querySelector(".answer-table__results");
        if (results) {
            const width = columns.filter(input => input.checked).reduce((sum, input) => sum + Number(input.dataset.columnWidth), 0);
            results.style.setProperty("--answer-table-width", `${width}px`);
        }
        columns.forEach(input => {
            root.querySelectorAll(`.answer-column-${input.dataset.columnToggle}`).forEach(cell => {
                cell.hidden = !visible.includes(input.dataset.columnToggle);
            });
        });
        try {
            sessionStorage.setItem(key, JSON.stringify({ visible }));
        } catch {
            /* Storage can be disabled. */
        }
    };
    columns.forEach(input => input.addEventListener("change", update));
    update();
    document.fonts?.ready.then(() => { measureWidths(); update(); });
    root.querySelectorAll("[data-submit]").forEach(input => input.addEventListener("change", () => {
        if (!input.classList.contains("select2-hidden-accessible")) input.form.requestSubmit();
    }));
    if (window.django?.jQuery) {
        window.django.jQuery(root).on("change", "select[data-submit].select2-hidden-accessible", function() {
            this.form.requestSubmit();
        });
    }
    const panels = [...root.querySelectorAll("[data-panel]")];
    let activePanel = panels.length ? "answer-filters" : "";
    try {
        const savedPanel = sessionStorage.getItem("answer-active-panel");
        if (savedPanel && panels.some(button => button.dataset.panel === savedPanel)) {
            activePanel = savedPanel;
        }
    } catch {
        /* Storage can be disabled. */
    }
    const syncPanels = () => {
        panels.forEach(button => {
            const panel = document.getElementById(button.dataset.panel);
            const open = button.dataset.panel === activePanel;
            button.setAttribute("aria-expanded", String(open));
            button.setAttribute("aria-pressed", String(open));
            panel.hidden = !open;
        });
    };
    panels.forEach(button => button.addEventListener("click", () => {
        activePanel = button.dataset.panel;
        try {
            sessionStorage.setItem("answer-active-panel", activePanel);
        } catch {
            /* Storage can be disabled. */
        }
        syncPanels();
    }));
    syncPanels();
});
