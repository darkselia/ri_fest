document.addEventListener("htmx:afterSwap", (event) => {
    if (!["modal-content", "dialog-form"].includes(event.target.id)) return;
    const dialog = event.target.closest("#dialog") || event.target.querySelector("#dialog");
    if (!dialog?.querySelector("[data-confirmation-dialog]")) return;
    const submit = dialog?.querySelector('button[type="submit"]');
    if (!submit) return;
    const cancel = submit.parentElement.querySelector("a");
    const title = dialog.querySelector("h1");
    submit.parentElement.prepend(submit);
    if (cancel) cancel.textContent = "Отмена";
    submit.disabled = Boolean(dialog.querySelector("[data-confirmation-blocked]"));
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    if (title) {
        title.id = "admin-confirmation-dialog-title";
        dialog.setAttribute("aria-labelledby", title.id);
    }
    requestAnimationFrame(() => cancel?.focus());
});

document.addEventListener("DOMContentLoaded", () => {
    const url = document.querySelector("[data-save-archive-url]")?.dataset.saveArchiveUrl;
    const form = document.getElementById("festival_form");
    const target = document.getElementById("modal-content");
    if (!url || !form || !target || !window.htmx || !window.Alpine) return;
    let bypass = false;
    let checking = false;
    form.addEventListener("submit", async (event) => {
        if (bypass) {
            bypass = false;
            return;
        }
        if (event.defaultPrevented || event.submitter?.name !== "_save") return;
        event.preventDefault();
        if (checking) return;
        checking = true;
        const submitter = event.submitter;
        const payload = new FormData(form);
        const original = JSON.stringify(Array.from(payload.entries()));
        try {
            const response = await fetch(url, {
                method: "POST",
                body: payload,
                credentials: "same-origin",
            });
            if (response.status === 200) {
                if (JSON.stringify(Array.from(new FormData(form).entries())) !== original) {
                    queueMicrotask(() => form.requestSubmit(submitter));
                    return;
                }
                target.innerHTML = await response.text();
                window.htmx.process(target);
                window.Alpine.initTree(target);
                window.Alpine.$data(target).openModal = true;
                window.htmx.trigger(target, "htmx:afterSwap");
                return;
            }
        } catch (_) {
            // The ordinary form submission still handles validation and confirmation.
        } finally {
            checking = false;
        }
        bypass = true;
        form.requestSubmit(submitter);
    });
});
