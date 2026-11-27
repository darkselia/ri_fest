document.addEventListener("DOMContentLoaded", () => {
    const field = document.querySelector('[name="blocks"]');
    const outline = document.querySelector("[data-block-outline]");
    const editor = document.querySelector("[data-block-editor]");
    if (!field || !outline || !editor) return;
    let blocks;
    try {
        blocks = JSON.parse(field.value || "[]");
    } catch (_) {
        blocks = [];
    }
    if (!Array.isArray(blocks)) blocks = [];
    const error = document.querySelector("[data-block-validation-error]")?.textContent.trim() || "";
    const errorIndex = Number(error.match(/Блок (\d+)/)?.[1]) - 1;
    let selected = errorIndex >= 0 && errorIndex < blocks.length ? errorIndex : 0;
    const narrow = window.matchMedia("(width < 1024px)");
    const types = {
        text: "Текст",
        image: "Изображение",
        file: "Файл",
        festival: "Фестиваль"
    };
    let pendingUploads = 0;
    document.getElementById("page_form").addEventListener("submit", event => {
        if (!pendingUploads) return;
        event.preventDefault();
        window.alert("Дождитесь завершения загрузки файла.");
    });
    const save = () => {
        field.value = JSON.stringify(blocks);
        field.dispatchEvent(new Event("input", {
            bubbles: true
        }));
    };
    const button = (label, icon, action) => {
        const el = document.querySelector("[data-page-button]").content.firstElementChild.cloneNode(true);
        el.type = "button";
        el.setAttribute("aria-label", label);
        el.title = label;
        if (icon) {
            window.schemaControls.icon(el, label, icon);
        } else el.querySelector("[data-button-label]").textContent = label;
        el.addEventListener("click", action);
        return el;
    };
    const move = (from, to) => {
        if (to < 0 || to >= blocks.length || from === to) return;
        blocks.splice(to, 0, blocks.splice(from, 1)[0]);
        selected = to;
        save();
        render();
    };
    window.riPageBlockSort = index => {
        const from = Number(index);
        const position = [...outline.querySelectorAll(":scope > [data-block-index]")]
            .findIndex(row => Number(row.dataset.blockIndex) === from);
        if (!Number.isInteger(from)) return;
        if (from < 0 || from >= blocks.length || position < 0 || position >= blocks.length || from === position) return;
        blocks.splice(position, 0, blocks.splice(from, 1)[0]);
        selected = position;
        save();
        requestAnimationFrame(render);
    };

    function render() {
        if (window.django?.jQuery?.fn.select2) {
            window.django.jQuery(editor).find(".select2-hidden-accessible").select2("destroy");
            window.django.jQuery(outline).find(".select2-hidden-accessible").select2("destroy");
        }
        document.querySelector("[data-block-count]").textContent = blocks.length;
        outline.replaceChildren();
        editor.replaceChildren();
        blocks.forEach((block, index) => {
            const row = document.querySelector("[data-block-row]").content.firstElementChild.cloneNode(true);
            row.dataset.blockIndex = index;
            row.setAttribute("x-sort:item", JSON.stringify(index));
            if (narrow.matches) {
                const expanded = index === selected;
                const toggle = button(expanded ? "Свернуть блок" : "Раскрыть блок", expanded ? "expand_less" : "expand_more", () => {
                    selected = expanded ? -1 : index;
                    render();
                });
                toggle.setAttribute("aria-expanded", String(expanded));
                row.prepend(toggle);
            }
            const handle = button("Переместить блок: перетащите или нажмите Alt + стрелку", "drag_indicator", () => {});
            handle.classList.add("schema-drag");
            handle.setAttribute("data-drag-handle", "");
            handle.addEventListener("keydown", event => {
                if (!event.altKey || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
                event.preventDefault();
                const target = index + (event.key === "ArrowUp" ? -1 : 1);
                move(index, target);
                outline.querySelector(`[data-block-index="${target}"] .schema-drag`)?.focus();
            });
            const choose = row.querySelector(".schema-outline__choose");
            choose.setAttribute("aria-pressed", index === selected ? "true" : "false");
            if (narrow.matches) choose.setAttribute("aria-expanded", index === selected ? "true" : "false");
            const number = row.querySelector(".schema-outline__number");
            number.textContent = index + 1;
            const summary = row.querySelector(".schema-outline__summary");
            summary.textContent = block.title || "Без названия";
            const type = row.querySelector(".schema-outline__type");
            type.textContent = types[block.kind];
            row.querySelector(".schema-hidden-note").hidden = block.visible;
            choose.addEventListener("click", () => {
                selected = narrow.matches && selected === index ? -1 : index;
                render();
            });
            choose.before(handle);
            outline.append(row);
        });
        const block = blocks[selected];
        if (!block) return;
        const card = document.querySelector("[data-block-card]").content.firstElementChild.cloneNode(true);
        if (!block.visible) card.classList.add("schema-card--hidden");
        if (selected === errorIndex && error) {
            const notice = card.querySelector("[data-block-error]");
            notice.hidden = false;
            notice.textContent = error;
        }

        function bind(control, key) {
            control.value = block[key] ?? "";
            control.addEventListener("input", () => {
                block[key] = control.value;
                save();
                if (key === "title") outline.querySelector(`[data-block-index="${selected}"] .schema-outline__summary`).textContent = block.title || "Без названия";
            });
            return control;
        }
        bind(card.querySelector('[data-field="title"]'), "title");
        const kind = card.querySelector('[data-field="kind"]');
        kind.value = block.kind;
        kind.addEventListener("change", () => {
            block.kind = kind.value;
            save();
            render();
        });
        if (block.kind === "text") {
            const text = document.querySelector("[data-page-text-widget]").content.cloneNode(true);
            const value = text.querySelector('input[type="hidden"]');
            const wysiwyg = text.querySelector("trix-editor");
            const wrapper = card.querySelector("[data-text-fields]");
            wrapper.hidden = false;
            value.value = block.text || "";
            value.removeAttribute("name");
            wysiwyg.setAttribute("aria-label", "Текст");
            wysiwyg.addEventListener("trix-file-accept", event => event.preventDefault());
            wysiwyg.addEventListener("trix-change", () => {
                block.text = value.value;
                save();
            });
            wrapper.append(text);
        }
        let fileSelect = null;
        if (block.kind === "image" || block.kind === "file") {
            const fileFields = card.querySelector("[data-file-fields]");
            fileFields.hidden = false;
            fileSelect = document.getElementById("block-file-options").cloneNode(true);
            fileSelect.hidden = false;
            fileSelect.className = "unfold-admin-autocomplete";
            fileSelect.dataset.theme = "admin-autocomplete";
            fileSelect.id = "page-block-file";
            fileSelect.setAttribute("aria-labelledby", "page-block-file-label");
            fileSelect.setAttribute("aria-required", "true");
            card.querySelector("[data-file-select]").replaceWith(fileSelect);
            if (block.kind === "image") {
                [...fileSelect.options].forEach(option => {
                    if (option.value && !/\.(png|jpe?g|webp|gif)$/i.test(option.dataset.filename || "")) option.remove();
                });
                fileSelect.value = block.file_id ?? "";
            }
            fileSelect.value = block.file_id ?? "";
            const uploadUrl = document.querySelector("[data-content-upload-url]")?.dataset.contentUploadUrl;
            if (uploadUrl) {
                const upload = card.querySelector("[data-upload]");
                if (block.kind === "image") upload.accept = ".png,.jpg,.jpeg,.webp,.gif";
                const uploadButton = button("Загрузить с устройства", "upload_file", () => upload.click());
                card.querySelector("[data-upload-button]").replaceWith(uploadButton);
                const status = card.querySelector("[data-upload-status]");
                upload.addEventListener("change", async () => {
                    if (!upload.files.length) return;
                    const payload = new FormData();
                    payload.set("file", upload.files[0]);
                    payload.set("kind", block.kind);
                    uploadButton.disabled = true;
                    status.textContent = "Загружаем файл…";
                    pendingUploads++;
                    try {
                        const response = await fetch(uploadUrl, {
                            method: "POST",
                            body: payload,
                            credentials: "same-origin",
                            headers: {
                                "X-CSRFToken": document.querySelector('#page_form [name="csrfmiddlewaretoken"]').value
                            }
                        });
                        let data;
                        try {
                            data = await response.json();
                        } catch (_) {
                            throw new Error("Не удалось загрузить файл. Проверьте права и соединение.");
                        }
                        if (!response.ok) throw new Error(data.error || "Не удалось загрузить файл.");
                        const option = new Option(data.name, data.id);
                        option.dataset.filename = upload.files[0].name;
                        document.getElementById("block-file-options").add(option);
                        block.file_id = String(data.id);
                        save();
                        render();
                    } catch (error) {
                        status.textContent = error.message || "Не удалось загрузить файл.";
                        uploadButton.disabled = false;
                    } finally {
                        pendingUploads--;
                    }
                });
            }
            const description = card.querySelector("[data-image-description]");
            description.hidden = block.kind !== "image";
            bind(description.querySelector("input"), "description");
        }
        if (block.kind === "festival") {
            const container = card.querySelector("[data-festival-fields]");
            container.hidden = false;
            const festival = document.getElementById("block-festival-options").cloneNode(true);
            festival.hidden = false;
            festival.removeAttribute("id");
            festival.className = "border border-base-200 bg-white dark:bg-base-900 dark:border-base-700 rounded-default px-3 py-2 w-full text-important";
            card.querySelector("[data-festival-select]").replaceWith(bind(festival, "festival_id"));
        }
        const tools = card.querySelector(".schema-card__tools");
        [
            ["Выше", -1],
            ["Ниже", 1]
        ].forEach(([label, step]) => {
            const action = button(`Переместить ${label.toLowerCase()}`, step < 0 ? "arrow_upward" : "arrow_downward", () => move(selected, selected + step));
            action.classList.add("content-mobile-move");
            action.disabled = selected + step < 0 || selected + step >= blocks.length;
            tools.append(action);
        });
        tools.append(
            button(block.visible ? "Скрыть блок" : "Показать блок", block.visible ? "visibility" : "visibility_off", () => {
                block.visible = !block.visible;
                save();
                render();
            }),
            button("Дублировать блок", "content_copy", () => {
                blocks.splice(selected + 1, 0, {
                    ...block
                });
                selected++;
                save();
                render();
            }),
            button("Удалить блок", "delete", () => {
                blocks.splice(selected, 1);
                selected = Math.max(0, selected - 1);
                save();
                render();
            })
        );
        if (narrow.matches) outline.querySelector(`[data-block-index="${selected}"]`).after(card);
        else editor.append(card);
        if (fileSelect && window.django?.jQuery?.fn.djangoCustomSelect2) {
            window.django.jQuery(fileSelect).djangoCustomSelect2().on("change", () => {
                block.file_id = fileSelect.value;
                save();
            });
            const selection = fileSelect.nextElementSibling?.querySelector(".select2-selection");
            selection?.setAttribute("aria-label", "Файл");
            selection?.setAttribute("aria-required", "true");
        }
    }
    document.querySelector("[data-block-add]").addEventListener("click", () => {
        blocks.push({
            kind: "text",
            title: "",
            text: "",
            description: "",
            visible: true
        });
        selected = blocks.length - 1;
        save();
        render();
    });
    document.querySelector("[data-page-copy]")?.addEventListener("click", async () => {
        const url = document.querySelector("[data-page-copy-source]").value;
        if (!url) return;
        try {
            const response = await fetch(url, {
                credentials: "same-origin"
            });
            if (!response.ok) throw new Error();
            const data = await response.json();
            if (blocks.length + data.blocks.length > 200) throw new Error();
            blocks.push(...data.blocks);
            selected = Math.max(0, blocks.length - data.blocks.length);
            save();
            render();
        } catch (_) {
            window.alert("Не удалось скопировать блоки. Проверьте доступ и количество блоков.");
        }
    });
    const preview = document.querySelector("[data-page-preview]");
    const dialog = document.getElementById("page-preview");
    preview.addEventListener("click", async () => {
        if (pendingUploads) {
            window.alert("Дождитесь завершения загрузки файла.");
            return;
        }
        save();
        const payload = new FormData(document.getElementById("page_form"));
        payload.set("blocks", JSON.stringify(blocks));
        try {
            const response = await fetch(document.querySelector("[data-page-preview-url]").dataset.pagePreviewUrl, {
                method: "POST",
                body: payload,
                credentials: "same-origin"
            });
            if (!response.ok) {
                window.alert(await response.text());
                return;
            }
            dialog.querySelector("iframe").srcdoc = await response.text();
            dialog.showModal();
        } catch (_) {
            window.alert("Не удалось открыть предпросмотр. Проверьте соединение.");
        }
    });
    dialog.querySelector("[data-close-page-preview]").onclick = () => dialog.close();
    narrow.addEventListener("change", render);
    render();
});
