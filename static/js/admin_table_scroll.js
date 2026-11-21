document.addEventListener("DOMContentLoaded", () => {
    const applicationList = document.body.matches(".app-applications.model-application.change-list");
    document.querySelectorAll("a[data-sort-new]").forEach(link => {
        if (applicationList) return;
        const url = new URL(link.href);
        const ordering = url.searchParams.get("o")?.split(".");
        if (ordering?.length) {
            ordering[0] = `-${ordering[0].replace(/^-/, "")}`;
            url.searchParams.set("o", ordering.join("."));
            link.href = url;
        }
    });
    const rail = document.querySelector(".admin-table-rail");
    const table = document.querySelector("#result_list, .answer-table__scroll table");
    if (!rail || !table) return;
    if (table.id === "result_list" && document.body.matches(".app-applications.model-application")) {
        const headers = table.querySelectorAll("thead th");
        let extraWidth = 0;
        ["total_display", "paid_display", "balance_display"].forEach(field => {
            const cells = [...table.querySelectorAll(`.field-${field}`)];
            if (!cells.length) return;
            const header = headers[cells[0].cellIndex];
            const needed = Math.max(...cells.map(cell => cell.scrollWidth));
            if (header && needed > header.clientWidth) {
                extraWidth += needed - header.clientWidth;
                header.style.width = `${needed}px`;
            }
        });
        if (extraWidth) table.style.minWidth = `${table.offsetWidth + extraWidth}px`;
    }
    const scroller = table.parentElement;
    const slider = rail.querySelector("input");
    const update = () => {
        const footer = rail.parentElement;
        const padding = parseFloat(getComputedStyle(footer).paddingLeft) || 0;
        rail.style.width = `${scroller.clientWidth}px`;
        rail.style.marginLeft = `${scroller.getBoundingClientRect().left - footer.getBoundingClientRect().left - padding}px`;
        slider.max = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
        rail.hidden = slider.max === "0";
        slider.value = scroller.scrollLeft;
    };
    scroller.addEventListener("scroll", () => {
        slider.value = scroller.scrollLeft;
    });
    slider.addEventListener("input", () => {
        scroller.scrollLeft = Number(slider.value);
    });
    const observer = new ResizeObserver(update);
    observer.observe(scroller);
    observer.observe(table);
    update();
});
