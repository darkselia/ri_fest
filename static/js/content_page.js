(() => {
    "use strict";

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("page_form");
        const discard = document.querySelector("[data-discard-changes]");
        const savedFields = document.getElementById("page-saved-fields");
        if (!form || !discard || !savedFields) return;

        const savedChanges = discard.dataset.hasSavedChanges === "true";
        const savedValues = JSON.parse(savedFields.textContent);
        const fields = ["title", "slug", "login_required", "blocks"]
            .map(name => form.elements.namedItem(name))
            .filter(Boolean);
        const normalizeBlocks = value => {
            try {
                return JSON.stringify(JSON.parse(value || "[]"));
            } catch (_) {
                return value;
            }
        };
        const updateDiscard = () => {
            const unsavedChanges = fields.some(field => (
                field.type === "checkbox" ?
                field.checked !== savedValues[field.name] :
                field.name === "blocks" ?
                normalizeBlocks(field.value) !== normalizeBlocks(savedValues.blocks) :
                field.value !== savedValues[field.name]
            ));
            discard.hidden = !savedChanges && !unsavedChanges;
        };
        form.addEventListener("input", updateDiscard);
        form.addEventListener("change", updateDiscard);
        updateDiscard();
    });
})();
