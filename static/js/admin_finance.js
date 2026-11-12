(() => {
    "use strict";
    let busy = false;
    let uncertain = null;
    const original = new WeakMap();
    const sync = () => {
        document.querySelectorAll(
            "[data-payment-form] input:not([type='hidden']), [data-payment-form] button, " +
            "[data-fee-row] select, [data-fee-row] input, [data-fee-row] button, [data-fee-form] button, [data-fee-refresh], " +
            "button[form='application_form']"
        ).forEach(control => {
            if (!original.has(control)) original.set(control, control.disabled);
            const refresh = control.matches("[data-payment-refresh], [data-fee-refresh]");
            const readonly = control.closest("[data-payment-form]")?.dataset.readonly === "true";
            control.disabled = original.get(control) || readonly || busy || (uncertain && !refresh);
        });
    };
    window.adminFinance = {
        begin(owner) {
            if (busy || (uncertain && uncertain !== owner)) return false;
            busy = true;
            sync();
            return true;
        },
        end(unknown = false, owner = null) {
            busy = false;
            uncertain = unknown ? owner : null;
            sync();
        },
        get blocked() {
            return busy || uncertain;
        }
    };
    document.addEventListener("submit", event => {
        if (event.target.id !== "application_form") return;
        if (busy || uncertain) event.preventDefault();
        else busy = true;
    }, true);
    window.addEventListener("pageshow", event => {
        if (event.persisted) {
            busy = false;
            sync();
        }
    });
})();
