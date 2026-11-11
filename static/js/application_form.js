(() => {
    "use strict";
    const form = document.querySelector("[data-application-form]");
    let dirty = false;
    form?.addEventListener("input", () => {
        dirty = true;
    });
    document.querySelectorAll("[data-auto-select] select").forEach(select => {
        let previous = select.value;
        let pending = null;
        select.addEventListener("change", () => {
            pending?.remove();
            if (dirty) {
                const selected = select.value;
                select.value = previous;
                const panel = document.createElement("div");
                panel.className = "application-switch-confirm";
                panel.setAttribute("role", "group");
                panel.setAttribute("aria-label", "Подтверждение переключения фестиваля");
                const text = document.createElement("p");
                text.textContent = "В форме есть несохранённые ответы. При переключении фестиваля они будут потеряны.";
                panel.append(text);
                const close = () => {
                    panel.remove();
                    pending = null;
                    select.focus();
                };
                for (const [caption, action] of [
                        ["Остаться", close],
                        ["Переключить", () => {
                            select.value = selected;
                            close();
                            select.form.requestSubmit();
                        }]
                    ]) {
                    const button = document.createElement("button");
                    button.type = "button";
                    button.className = "button button--secondary";
                    button.textContent = caption;
                    button.addEventListener("click", action);
                    panel.append(button);
                }
                panel.addEventListener("keydown", event => {
                    if (event.key === "Escape") {
                        event.preventDefault();
                        close();
                    }
                });
                select.form.append(panel);
                pending = panel;
                panel.querySelector("button").focus();
                return;
            }
            previous = select.value;
            select.form.requestSubmit();
        });
    });
    if (!form) return;
    const output = form.querySelector("[data-application-total]");
    if (!output) return;
    const money = value => `${(value / 100n).toLocaleString("ru-RU")},${(value % 100n).toString().padStart(2, "0")} ₽`;
    const update = () => {
        let total = BigInt(output.dataset.fixedKopecks || "0");
        form.querySelectorAll("[data-date-fee-kopecks]").forEach(block => {
            total += BigInt(block.dataset.dateFeeKopecks);
        });
        form.querySelectorAll("[data-service-group]").forEach(group => {
            let subtotal = 0n;
            group.querySelectorAll("input[data-kopecks]:checked").forEach(box => {
                subtotal += BigInt(box.dataset.kopecks);
            });
            const target = group.querySelector("[data-group-total]");
            if (target) target.textContent = money(subtotal);
            total += subtotal;
        });
        output.textContent = money(total);
    };
    const refusal = group => {
        const boxes = [...group.querySelectorAll('input[type="checkbox"][data-kopecks]')];
        const optedOut = boxes.some(box => box.dataset.optOut && box.checked);
        boxes.forEach(box => {
            if (!box.dataset.optOut) {
                if (optedOut) box.checked = false;
                box.disabled = optedOut;
            }
        });
    };
    form.querySelectorAll("[data-service-group]").forEach(refusal);
    form.addEventListener("change", event => {
        const group = event.target.closest("[data-service-group]");
        if (group) refusal(group);
        update();
    });
    update();
})();
