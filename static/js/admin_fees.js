(() => {
    "use strict";
    const container = document.querySelector("[data-fees-container]");
    if (!container) return;
    const endpoint = new URL(location.href);
    endpoint.search = "";
    const answerVersion = document.querySelector("[data-application-form] input[name='version']");
    let uncertain = false;
    const prepareRows = () => {
        container.querySelectorAll("[data-fee-row]").forEach(row => {
            const checkbox = row.querySelector("[data-fee-toggle]");
            const select = row.querySelector("select");
            if (!checkbox || !select) return;
            checkbox.closest("label").hidden = false;
            row.dataset.locked = String(checkbox.checked);
            row.dataset.selected = select.value;
        });
    };
    prepareRows();
    const show = messages => {
        const notice = container.querySelector("[data-fee-status]");
        if (!notice) return;
        notice.replaceChildren(...messages.filter(Boolean).map(message => {
            const p = document.createElement("p");
            p.textContent = message;
            return p;
        }));
    };
    const send = async (url, options, fieldId, focusSelector = "[data-fee-toggle]") => {
        if (!window.adminFinance.begin("fees")) return;
        container.setAttribute("aria-busy", "true");
        try {
            const response = await fetch(url, {
                ...options,
                headers: {
                    Accept: "application/json"
                },
                credentials: "same-origin",
                cache: "no-store",
                signal: AbortSignal.timeout(15000)
            });
            if (response.redirected || !response.headers.get("Content-Type")?.includes("application/json")) throw new Error();
            const data = await response.json();
            if (typeof data.html !== "string" || !Array.isArray(data.errors) || !Array.isArray(data.blocks)) throw new Error();
            container.innerHTML = data.html;
            prepareRows();
            for (const block of data.blocks) {
                const target = document.querySelector(`[data-application-form] [data-fee-field="${Number(block.id)}"]`);
                if (target) target.outerHTML = block.html;
            }
            const balance = document.querySelector("[data-balance]");
            if (balance && data.paid_kopecks !== undefined) balance.dataset.paidKopecks = data.paid_kopecks;
            document.querySelector("[data-application-form]")?.dispatchEvent(new Event("fees-updated"));
            if (answerVersion && data.answer_version) answerVersion.value = data.answer_version;
            uncertain = false;
            show([
                ...data.errors, data.message, data.warning,
                options.method === "GET" && "Актуальные отметки загружены. Перед сохранением ответов проверьте, не изменились ли данные в другом окне."
            ]);
            const focus = container.querySelector(`[data-fee-row="${Number(fieldId)}"] ${focusSelector}`);
            focus?.focus({
                preventScroll: true
            });
        } catch {
            uncertain = true;
            const row = container.querySelector(`[data-fee-row="${Number(fieldId)}"]`);
            if (row) {
                row.querySelector("[data-fee-toggle]").checked = row.dataset.locked === "true";
                row.querySelector("select").value = row.dataset.selected;
            }
            show(["Не удалось подтвердить результат. Обновите отметки перед следующей операцией: изменение могло уже сохраниться."]);
        } finally {
            container.setAttribute("aria-busy", "false");
            const refresh = container.querySelector("[data-fee-refresh]");
            if (refresh) refresh.hidden = !uncertain;
            window.adminFinance.end(uncertain, "fees");
        }
    };
    container.addEventListener("change", event => {
        const control = event.target;
        const row = control.closest("[data-fee-row]");
        if (!row || !control.matches("[data-fee-toggle], select")) return;
        const checkbox = row.querySelector("[data-fee-toggle]");
        const select = row.querySelector("select");
        if (!checkbox || !select || uncertain || window.adminFinance.blocked) return;
        if (control === select && !checkbox.checked) {
            row.dataset.selected = select.value;
            return;
        }
        const data = new FormData(select.form);
        const action = control === checkbox ? (checkbox.checked ? "lock" : "unlock") : "replace";
        data.set("fee_action", action);
        if (answerVersion) data.set("answers_version", answerVersion.value);
        send(endpoint, { method: "POST", body: data }, data.get("field_id"),
            control === select ? "select" : "[data-fee-toggle]");
    });
    container.addEventListener("submit", event => {
        const form = event.target.closest("[data-fee-form]");
        if (!form) return;
        event.preventDefault();
        if (uncertain || window.adminFinance.blocked) return;
        const data = new FormData(form);
        data.set("fee_action", event.submitter?.value || "lock");
        if (answerVersion) data.set("answers_version", answerVersion.value);
        send(endpoint, {
            method: "POST",
            body: data
        }, data.get("field_id"));
    });
    container.addEventListener("click", event => {
        if (event.target.closest("[data-fee-refresh]")) {
            const url = new URL(endpoint);
            url.searchParams.set("fee_status", "1");
            send(url, {
                method: "GET"
            });
        }
    });
})();
