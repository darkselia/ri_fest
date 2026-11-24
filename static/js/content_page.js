(() => {
    "use strict";

    document.addEventListener("DOMContentLoaded", () => {
        const form = document.getElementById("page_form");
        const discard = document.querySelector("[data-discard-changes]");
        const savedFields = document.getElementById("page-saved-fields");
        if (!form || !discard || !savedFields) return;

        const savedChanges = discard.dataset.hasSavedChanges === "true";
        const savedValues = JSON.parse(savedFields.textContent);
        const fields = ["title", "slug", "login_required"]
            .map(name => form.elements.namedItem(name))
            .filter(Boolean);
        const updateDiscard = () => {
            const unsavedChanges = fields.some(field => (
                field.type === "checkbox"
                    ? field.checked !== savedValues[field.name]
                    : field.value !== savedValues[field.name]
            ));
            discard.hidden = !savedChanges && !unsavedChanges;
        };
        form.addEventListener("input", updateDiscard);
        form.addEventListener("change", updateDiscard);
        updateDiscard();
    });
})();
