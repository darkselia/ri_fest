document.addEventListener("DOMContentLoaded", () => {
    const trigger = document.querySelector("[data-filter-drawer-open]");
    const panel = trigger && document.getElementById(trigger.getAttribute("aria-controls"));
    if (!trigger || !panel) return;
    document.documentElement.classList.add("filter-drawer-ready");

    const compact = matchMedia("(width < 1024px)");
    const panelKey = `ri-fest:filter-panel:${location.pathname}`;
    const drawerKey = `ri-fest:filter-drawer:${location.pathname}`;
    const read = key => {
        try {
            return sessionStorage.getItem(key);
        } catch {
            return null;
        }
    };
    const save = (key, open) => {
        try {
            sessionStorage.setItem(key, open ? "open" : "closed");
        } catch {
            /* Storage can be disabled. */
        }
    };
    const setExpanded = open => {
        trigger.setAttribute("aria-expanded", String(open));
        const label = open ? "Скрыть фильтры" : "Показать фильтры";
        trigger.setAttribute("aria-label", label);
        trigger.title = label;
    };
    const setPanel = open => {
        panel.hidden = !open;
        panel.closest(".answer-table__layout")?.classList.toggle("answer-table__layout--full", !open);
        setExpanded(open);
    };

    const marker = document.createComment("filter panel position");
    panel.before(marker);
    const dialog = document.createElement("dialog");
    dialog.className = "filter-drawer";
    dialog.setAttribute("aria-label", "Фильтры");
    const header = document.createElement("div");
    header.className = "filter-drawer__header";
    const heading = document.createElement("h2");
    heading.textContent = "Фильтры";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "filter-drawer__close";
    close.setAttribute("aria-label", "Закрыть фильтры");
    close.textContent = "×";
    header.append(heading, close);
    dialog.append(header);
    document.body.append(dialog);

    const openDrawer = () => {
        if (dialog.open) return;
        dialog.showModal();
        document.documentElement.classList.add("filter-drawer-open");
        setExpanded(true);
        save(drawerKey, true);
        close.focus();
    };
    close.addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", event => {
        if (event.target !== dialog) return;
        const bounds = dialog.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right ||
            event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    });
    dialog.addEventListener("close", () => {
        document.documentElement.classList.remove("filter-drawer-open");
        save(drawerKey, false);
        setExpanded(!compact.matches && !panel.hidden);
        if (compact.matches) trigger.focus();
    });
    trigger.addEventListener("click", () => {
        if (compact.matches) {
            openDrawer();
        } else {
            const open = panel.hidden;
            setPanel(open);
            save(panelKey, open);
        }
    });

    const sync = () => {
        if (compact.matches) {
            setPanel(true);
            panel.before(marker);
            dialog.append(panel);
            setExpanded(false);
            if (read(drawerKey) === "open") openDrawer();
        } else {
            if (dialog.open) dialog.close();
            if (dialog.contains(panel)) marker.after(panel);
            setPanel(read(panelKey) !== "closed");
        }
    };
    compact.addEventListener("change", sync);
    sync();
});
