window.schemaControls = {
    caption(text, required = false) {
        const caption = document.createElement("span");
        caption.textContent = text;
        if (required) {
            const star = document.createElement("span");
            star.className = "text-red-600 dark:text-red-400";
            star.textContent = " *";
            caption.append(star);
        }
        return caption;
    },
    icon(button, label, icon) {
        const symbol = document.createElement("span");
        symbol.className = "material-symbols-outlined";
        symbol.textContent = icon;
        button.replaceChildren(symbol);
        button.classList.add("schema-editor-icon");
        button.setAttribute("aria-label", label);
        button.title = label;
        return button;
    },
};
