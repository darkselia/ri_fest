(() => {
    "use strict";
    const init = () => {
        const publish = document.querySelector("[data-publish]");
        const publication = document.querySelector("[data-publication-url]");
        const festivalForm = document.getElementById("festival_form");
        let checkVersion = 0;
        let timer;
        const reasons = document.getElementById("publish-reasons");
        const setPublishDisabled = disabled => {
            if (!publish) return;
            publish.disabled = disabled;
            publish.classList.toggle("cursor-not-allowed", disabled);
            publish.classList.toggle("opacity-50", disabled);
            publish.classList.toggle("cursor-pointer", !disabled);
            publish.setAttribute("aria-disabled", String(disabled));
        };
        const checkPublication = async () => {
            const current = ++checkVersion;
            if (!publish || !publication) return;
            setPublishDisabled(true);
            reasons.textContent = "Проверяем готовность публикации…";
            const payload = Object.fromEntries(new FormData(festivalForm));
            payload.festival_id = Number(publication.dataset.festivalId) || null;
            try {
                payload.schema = JSON.parse(payload.schema || "null");
                const response = await fetch(publication.dataset.publicationUrl, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": payload.csrfmiddlewaretoken
                    },
                    body: JSON.stringify(payload)
                });
                if (!response.ok || response.redirected) throw new Error();
                const data = await response.json();
                if (current !== checkVersion) return;
                setPublishDisabled(!data.ready);
                reasons.textContent = data.errors.join("; ");
            } catch {
                if (current === checkVersion) reasons.textContent = "Не удалось проверить готовность. Сохраните черновик и обновите страницу.";
            }
        };
        const scheduleCheck = () => {
            if (!publish) return;
            ++checkVersion;
            setPublishDisabled(true);
            reasons.textContent = "Проверяем готовность публикации…";
            clearTimeout(timer);
            timer = setTimeout(checkPublication, 350);
        };
        festivalForm?.addEventListener("input", event => {
            if (!event.target.closest("[data-schema-preview]")) scheduleCheck();
        });
        festivalForm?.addEventListener("change", event => {
            if (!event.target.closest("[data-schema-preview]")) scheduleCheck();
        });
        const root = document.getElementById("schema-editor");
        if (!root) return;
        const input = root.querySelector('[name="schema"]');
        const types = JSON.parse(document.getElementById("schema-types").textContent);
        const editable = root.dataset.editable === "true";
        const container = root.querySelector("[data-schema-fields]");
        const outline = root.querySelector("[data-schema-outline]");
        const desktop = window.matchMedia("(min-width: 801px)");
        const preview = root.querySelector("[data-schema-preview]");
        const dialog = document.getElementById("schema-preview-dialog");
        const previewFrame = dialog.querySelector("iframe");
        const hidingInput = root.querySelector('[name="schema_hiding"]');
        let hiding = {
            counts: {},
            option_counts: {},
            active: [],
            active_options: [],
            confirmed: []
        };
        try {
            hiding = {
                ...hiding,
                ...JSON.parse(hidingInput.value || "{}")
            };
        } catch {
            /* Server rechecks confirmation. */
        }
        const message = root.querySelector('[data-notice="schema"]');
        const copyMessage = root.querySelector('[data-notice="copy"]');
        const previewMessage = root.querySelector('[data-notice="preview"]');
        const showMessage = (target, text, error = true) => {
            target.textContent = text;
            target.classList.toggle("schema-notice--error", error);
            target.hidden = !text;
        };
        let rows;
        try {
            rows = JSON.parse(input.value || "[]") || [];
        } catch {
            rows = [];
            showMessage(message, "Некорректные данные шаблона. Исправьте ошибку перед сохранением.");
        }
        let version = 0;
        let previewVersion = 0;
        const node = (tag, text, className) => {
            const el = document.createElement(tag);
            if (text !== undefined) el.textContent = text;
            if (className) el.className = className;
            return el;
        };
        const sync = () => {
            input.value = JSON.stringify(rows);
            root.querySelector("[data-schema-count]").textContent = `(${rows.length})`;
            version += 1;
            scheduleCheck();
            if (preview.childElementCount) {
                preview.replaceChildren(node("p", "Шаблон изменён. Обновите предпросмотр."));
            }
        };
        const button = (label, action, disabled = false) => {
            const el = root.querySelector("[data-editor-button]").content.firstElementChild.cloneNode(true);
            el.querySelector("[data-button-label]").textContent = label;
            el.type = "button";
            el.disabled = !editable || disabled;
            el.addEventListener("click", action);
            return el;
        };
        let confirmation = null;
        const countIn = count => `${count} ${count % 10 === 1 && count % 100 !== 11 ? "анкете" : "анкетах"}`;
        const closeConfirmation = (restore = true) => {
            if (!confirmation) return;
            const {
                panel,
                trigger
            } = confirmation;
            confirmation = null;
            panel.remove();
            trigger.setAttribute("aria-expanded", "false");
            if (restore && trigger.isConnected) trigger.focus();
        };
        const confirmAction = (trigger, text, caption, action) => {
            closeConfirmation(false);
            const panel = node("div", undefined, "schema-confirm admin-panel");
            panel.setAttribute("role", "group");
            panel.setAttribute("aria-label", "Подтверждение действия");
            panel.append(node("p", text));
            const actions = node("div", undefined, "schema-confirm__actions");
            const cancel = button(action ? "Отмена" : "Закрыть", () => closeConfirmation());
            actions.append(cancel);
            if (action) actions.append(button(caption, () => {
                const owner = trigger.closest("[data-editor-id]")?.dataset.editorId;
                const group = trigger.parentElement.classList.contains("schema-option__actions") ? ".schema-option__actions" : ".schema-card__tools";
                const index = [...trigger.parentElement.children].filter(el => el.tagName === "BUTTON").indexOf(trigger);
                closeConfirmation();
                action();
                requestAnimationFrame(() => {
                    if (confirmation) return;
                    const target = container.querySelector(`[data-editor-id="${owner}"] ${group}`)?.querySelectorAll("button")[index];
                    (target || root.querySelector("[data-add-field]"))?.focus();
                });
            }));
            panel.append(actions);
            trigger.parentElement.append(panel);
            trigger.setAttribute("aria-expanded", "true");
            confirmation = {
                panel,
                trigger
            };
            cancel.focus();
        };
        document.addEventListener("pointerdown", event => {
            if (confirmation && !confirmation.panel.contains(event.target) && !confirmation.trigger.contains(event.target)) closeConfirmation(false);
        });
        document.addEventListener("keydown", event => {
            if (event.key === "Escape" && confirmation) {
                event.preventDefault();
                closeConfirmation();
            }
        });
        const acknowledge = key => {
            if (!hiding.confirmed.includes(key)) hiding.confirmed.push(key);
            hidingInput.value = JSON.stringify(hiding);
        };
        const feeCancelText = () => `Плата будет отменена у всех анкет фестиваля (${hiding.fee_count || 0}). Внесённые деньги сохранятся. Повторное включение применит текущий тариф без прежней фиксации.`;
        const questionHideText = row => row.field_type === "datefee" ? feeCancelText() : `Анкет с ответами: ${hiding.counts[String(row.id)] || 0}. Вопрос и сохранённые ответы будут скрыты от участников. В новых анкетах вопрос не появится; данные сохранятся.`;
        const hideQuestion = (row, trigger) => confirmAction(trigger, questionHideText(row), "Скрыть вопрос", () => {
            if (row.id) acknowledge(String(row.id));
            row.is_active = false;
            sync();
            render();
        });
        const control = (parent, object, name, title, type = "text", values = null, required = false) => {
            const label = node("label", undefined, type === "checkbox" ? "schema-check" : "schema-control text-subtle");
            if (type !== "checkbox") {
                label.append(window.schemaControls.caption(title, required));
            }
            label.dataset.control = name;
            const widget = root.querySelector(`[data-editor-control="${values ? "select" : type}"]`).content.cloneNode(true);
            const el = widget.querySelector("input, select, textarea");
            el.removeAttribute("name");
            el.removeAttribute("id");
            el.required = false;
            if (values) {
                el.replaceChildren();
                values.forEach(([value, caption]) => {
                    const o = node("option", caption);
                    o.value = value;
                    el.append(o);
                });
            } else {
                if (type !== "textarea") el.type = type;
                else el.rows = 1;
                if (type === "number") {
                    el.min = "0";
                    el.step = "0.01";
                }
            }
            el.disabled = !editable;
            if (type === "checkbox") el.checked = Boolean(object[name]);
            else el.value = object[name] ?? "";
            el.addEventListener(type === "checkbox" || values ? "change" : "input", () => {
                object[name] = type === "checkbox" ? el.checked : el.value;
                sync();
            });
            label.append(widget);
            if (type === "checkbox") label.append(node("span", title));
            parent.append(label);
            return el;
        };
        const independent = (row) => {
            const copy = structuredClone(row);
            copy.id = null;
            [...(copy.options || []), ...(copy.services || []), ...(copy.fee_periods || [])].forEach(o => {
                o.id = null;
            });
            return copy;
        };
        const ids = new WeakMap();
        const expanded = new WeakSet();
        let selected = rows[0];
        let serial = 0;
        const identity = object => {
            if (!ids.has(object)) ids.set(object, `editor-${++serial}`);
            return ids.get(object);
        };
        const iconButton = (label, icon, action) => {
            const el = button("", action);
            return window.schemaControls.icon(el, label, icon);
        };
        const reorder = (list, id, position) => {
            const from = list.findIndex(item => identity(item) === id);
            if (from < 0 || from === position || list[from].is_opt_out) return;
            const refusal = list.findIndex(item => item.is_opt_out);
            if (refusal >= 0) position = Math.min(position, refusal - 1);
            if (position < 0) return;
            list.splice(position, 0, list.splice(from, 1)[0]);
            sync();
            requestAnimationFrame(render);
        };
        window.riSchemaSort = (id, position) => {
            if (editable) reorder(rows, id, position);
        };
        window.riSchemaOptionSort = (owner, id, position) => {
            const row = rows.find(item => identity(item) === owner);
            if (editable && row) reorder(["service", "service1"].includes(row.field_type) ? row.services : row.options, id, position);
        };
        const handle = (list, object) => {
            const el = iconButton("Переместить: перетащите или нажмите Alt + ↑ / ↓", "drag_indicator", () => {});
            el.classList.add("schema-drag");
            el.setAttribute("data-drag-handle", "");
            el.addEventListener("keydown", event => {
                if (!event.altKey || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
                event.preventDefault();
                const from = list.indexOf(object);
                const to = from + (event.key === "ArrowUp" ? -1 : 1);
                if (to < 0 || to >= list.length || list[to].is_opt_out || object.is_opt_out) return;
                list.splice(to, 0, list.splice(from, 1)[0]);
                sync();
                render();
                (desktop.matches ? outline : container).querySelector(`[data-editor-id="${identity(object)}"] [data-drag-handle]`)?.focus();
                root.querySelector("[data-sort-message]").textContent = `Новая позиция: ${to + 1}.`;
            });
            return el;
        };
        const render = () => {
            closeConfirmation(false);
            container.replaceChildren();
            outline.replaceChildren();
            if (!rows.includes(selected)) selected = rows[0];
            let questionNumber = 0;
            rows.forEach((row, i) => {
                row.options ||= [];
                row.services ||= [];
                const rowId = identity(row);
                const card = root.querySelector("[data-schema-card]").content.firstElementChild.cloneNode(true);
                if (!row.is_active) card.classList.add("schema-card--hidden");
                if (row.field_type === "info") card.classList.add("schema-card--info");
                if (row.field_type !== "info") questionNumber += 1;
                const entry = root.querySelector("[data-schema-row]").content.firstElementChild.cloneNode(true);
                entry.dataset.editorId = rowId;
                entry.setAttribute("x-sort:item", JSON.stringify(rowId));
                const choose = entry.querySelector(".schema-outline__choose");
                if (editable) choose.before(handle(rows, row));
                choose.setAttribute("aria-pressed", String(selected === row));
                entry.querySelector(".schema-outline__number").textContent = row.field_type === "info" ? "" : `${questionNumber}`;
                const outlineSummary = entry.querySelector(".schema-outline__summary");
                outlineSummary.textContent = row.label || "Без названия";
                if (row.is_active) entry.querySelector(".schema-hidden-note").remove();
                else entry.querySelector(".schema-hidden-note").hidden = false;
                entry.querySelector(".schema-outline__type").textContent = types.find(([value]) => value === row.field_type)?.[1] || row.field_type;
                choose.addEventListener("click", () => {
                    selected = row;
                    render();
                    outline.querySelector(`[data-editor-id="${rowId}"] .schema-outline__choose`)?.focus();
                });
                outline.append(entry);
                card.setAttribute("aria-label", row.field_type === "info" ? "Текст блок" : `${questionNumber}`);
                card.dataset.number = row.field_type === "info" ? "" : String(questionNumber);
                card.setAttribute("x-sort:item", JSON.stringify(rowId));
                card.dataset.editorId = rowId;
                const title = card.querySelector(".schema-card__title");
                const toggle = iconButton("Раскрыть вопрос", "expand_more", () => {
                    if (expanded.has(row)) expanded.delete(row);
                    else expanded.add(row);
                    setExpanded();
                });
                const setExpanded = () => {
                    const open = desktop.matches || expanded.has(row);
                    card.hidden = desktop.matches && selected !== row;
                    [...card.children].slice(1).forEach(child => child.hidden = !open);
                    toggle.setAttribute("aria-expanded", String(open));
                    toggle.setAttribute("aria-label", open ? "Свернуть вопрос" : "Раскрыть вопрос");
                    toggle.title = open ? "Свернуть вопрос" : "Раскрыть вопрос";
                    toggle.firstElementChild.textContent = open ? "expand_less" : "expand_more";
                    title.classList.toggle("schema-card__title--open", open);
                };
                toggle.disabled = false;
                title.prepend(toggle);
                if (editable) toggle.after(handle(rows, row));
                title.querySelector("[data-card-number]").textContent = row.field_type === "info" ? "Текст" : `${questionNumber}`;
                const summary = title.querySelector(".schema-card__summary");
                summary.textContent = row.label || "Без названия";
                if (row.is_active) title.querySelector(".schema-hidden-note").remove();
                else title.querySelector(".schema-hidden-note").hidden = false;
                title.querySelector(".schema-card__type").textContent = types.find(([value]) => value === row.field_type)?.[1] || row.field_type;
                const head = node("div", undefined, "schema-card__head");
                control(head, row, "label", "Название", "text", null, true).addEventListener("input", () => {
                    summary.textContent = row.label || "Без названия";
                    outlineSummary.textContent = row.label || "Без названия";
                });
                const originalKind = row.field_type;
                const kind = control(head, row, "field_type", "Тип", "text", types, true);
                const kindMessage = root.querySelector("[data-kind-message]").content.firstElementChild.cloneNode(true);
                head.append(kindMessage);
                kind.addEventListener("change", () => {
                    if ((hiding.counts[String(row.id)] || 0) > 0 && row.field_type !== originalKind) {
                        row.field_type = originalKind;
                        kind.value = originalKind;
                        sync();
                        showMessage(kindMessage, `Вопрос заполнен в ${countIn(hiding.counts[String(row.id)])}. Чтобы использовать другой тип, добавьте новый вопрос.`);
                        return;
                    }
                    if (["info", "datefee"].includes(row.field_type)) row.is_required = false;
                    if (row.field_type !== "text") row.in_application_name = false;
                    if (originalKind === "datefee" && row.field_type !== originalKind && row.id) {
                        const nextKind = row.field_type;
                        row.field_type = originalKind;
                        confirmAction(kind, feeCancelText(), "Сменить тип и отменить плату", () => {
                            acknowledge(String(row.id));
                            row.field_type = nextKind;
                            row.fee_periods = [];
                            sync();
                            render();
                        });
                        return;
                    }
                    if (row.field_type !== "datefee") row.fee_periods = [];
                    sync();
                    render();
                });
                card.append(head);
                if (row.field_type === "info") {
                    const wrapper = node("div", undefined, "schema-control");
                    const widget = root.querySelector("[data-schema-text-widget]").content.cloneNode(true);
                    const value = widget.querySelector('input[type="hidden"]');
                    const wysiwyg = widget.querySelector("trix-editor");
                    value.id = `schema-info-text-${i}`;
                    value.value = row.help_text || "";
                    value.removeAttribute("name");
                    wysiwyg.setAttribute("input", value.id);
                    wysiwyg.setAttribute("aria-label", "Текст и ссылки");
                    if (!editable) wrapper.inert = true;
                    wysiwyg.addEventListener("trix-file-accept", event => event.preventDefault());
                    wysiwyg.addEventListener("trix-change", () => {
                        row.help_text = value.value;
                        sync();
                    });
                    wrapper.append(node("span", "Текст и ссылки"), widget);
                    card.append(wrapper);
                } else {
                    control(card, row, "help_text", "Пояснение", "textarea");
                }
                const settings = node("div", undefined, "schema-flags");
                if (!["info", "datefee"].includes(row.field_type)) control(settings, row, "is_required", "Обязательно", "checkbox");
                if (row.field_type === "text") control(settings, row, "in_application_name", "Использовать для идентификации участника", "checkbox");
                if (!row.is_active) settings.append(node("span", "Скрыт от участников", "schema-hidden-note"));
                const priced = ["service", "service1"].includes(row.field_type);
                if (row.field_type === "datefee") {
                    row.fee_periods ||= [];
                    const periods = root.querySelector("[data-schema-periods]").content.firstElementChild.cloneNode(true);
                    row.fee_periods.forEach((period, index) => {
                        const line = root.querySelector("[data-schema-period]").content.firstElementChild.cloneNode(true);
                        line.querySelector("[data-period-title]").textContent = period.ends_on === null ? "Последний тариф без конечной даты" : "Тариф до даты";
                        if (period.ends_on !== null) control(line, period, "ends_on", "До включительно", "date", null, true);
                        control(line, period, "amount", "Сумма, ₽", "number", null, true);
                        if (editable) line.append(iconButton("Удалить тариф", "delete", () => {
                            row.fee_periods.splice(index, 1);
                            sync();
                            render();
                        }));
                        periods.append(line);
                    });
                    if (editable) {
                        if (!row.fee_periods.some(p => p.ends_on === null)) periods.append(button("Добавить последний тариф", () => {
                            row.fee_periods.push({
                                id: null,
                                ends_on: null,
                                amount: "0.00"
                            });
                            sync();
                            render();
                        }));
                        periods.append(button("Добавить тариф до даты", () => {
                            const last = row.fee_periods.findIndex(p => p.ends_on === null);
                            row.fee_periods.splice(last < 0 ? row.fee_periods.length : last, 0, {
                                id: null,
                                ends_on: "",
                                amount: "0.00"
                            });
                            sync();
                            render();
                        }));
                    }
                    card.append(periods);
                }
                if (priced || ["single", "multi"].includes(row.field_type)) {
                    const list = priced ? row.services : row.options;
                    if (priced) list.sort((a, b) => Number(Boolean(a.is_opt_out)) - Number(Boolean(b.is_opt_out)));
                    const variants = root.querySelector("[data-schema-variants]").content.firstElementChild.cloneNode(true);
                    variants.querySelector("[data-variants-title]").textContent = priced ? "Варианты услуг" : "Варианты ответа";
                    const sortable = variants.querySelector("[data-variants-list]");
                    if (editable) {
                        sortable.setAttribute("x-sort", `window.riSchemaOptionSort('${rowId}', $item, $position)`);
                        sortable.setAttribute("x-sort:config", "{handle: '[data-drag-handle]', animation: 150}");
                    }
                    list.forEach((option, j) => {
                        const optionCount = hiding.option_counts?.[`${priced ? "s" : "a"}:${option.id}`] || 0;
                        const line = root.querySelector("[data-schema-option]").content.firstElementChild.cloneNode(true);
                        line.classList.add(priced ? "schema-option--service" : "schema-option--choice");
                        if (!option.is_opt_out) line.setAttribute("x-sort:item", JSON.stringify(identity(option)));
                        else line.classList.add("schema-option--refusal");
                        line.dataset.editorId = identity(option);
                        if (editable && !option.is_opt_out) line.append(handle(list, option));
                        control(line, option, "label", "Подпись", "text", null, true);
                        if (priced) {
                            if (option.is_opt_out) {
                                line.prepend(node("span", "Отказ от услуги · бесплатно", "schema-refusal-note"));
                            } else {
                                control(line, option, "service_date", "Дата", "date");
                                control(line, option, "price", "Цена, ₽", "number", null, true);
                            }
                        }
                        if (!option.is_active) {
                            line.classList.add("schema-option--hidden");
                            line.append(node("span", "Скрыт", "schema-hidden-note"));
                        }
                        if (editable) {
                            const tools = node("div", undefined, "schema-option__actions");
                            const key = `${priced ? "s" : "a"}:${option.id}`;
                            const hideOption = trigger => confirmAction(trigger,
                                `Вариант выбран в ${countIn(optionCount)}. ` + (priced ?
                                    "Сохранённые выборы и их стоимость останутся в анкетах. Для нового выбора услуга будет скрыта." :
                                    "Сохранённый выбор останется в ответах; при следующей полной проверке анкеты потребуется выбрать доступный вариант."),
                                "Скрыть вариант", () => {
                                    if (option.id) acknowledge(key);
                                    option.is_active = false;
                                    sync();
                                    render();
                                });
                            const visibility = iconButton(option.is_active ? "Скрыть вариант" : "Показать вариант", option.is_active ? "visibility" : "visibility_off", event => {
                                if (option.is_active) hideOption(event.currentTarget);
                                else {
                                    option.is_active = true;
                                    sync();
                                    render();
                                }
                            });
                            tools.append(visibility, iconButton("Удалить вариант", "delete", event => {
                                if (optionCount) confirmAction(event.currentTarget, `Вариант выбран в ${countIn(optionCount)}. Для сохранения этих данных используйте скрытие варианта.`,
                                    option.is_active ? "Скрыть вариант" : null, option.is_active ? () => hideOption(visibility) : null);
                                else confirmAction(event.currentTarget, "Вариант не выбран ни в одной анкете. После сохранения фестиваля он будет удалён из шаблона.", "Удалить вариант", () => {
                                    list.splice(list.indexOf(option), 1);
                                    sync();
                                    render();
                                });
                            }));
                            line.append(tools);
                        }
                        sortable.append(line);
                    });
                    if (editable) variants.append(button("Добавить вариант", () => {
                        list.push(priced ? {
                            id: null,
                            label: "",
                            service_date: null,
                            price: "0.00",
                            is_active: true
                        } : {
                            id: null,
                            label: "",
                            is_active: true
                        });
                        sync();
                        render();
                    }));
                    if (editable && priced && !list.some(option => option.is_opt_out)) variants.append(button("Добавить отказ от услуги", () => {
                        list.push({
                            id: null,
                            label: "",
                            service_date: null,
                            price: "0.00",
                            is_active: true,
                            is_opt_out: true
                        });
                        sync();
                        render();
                    }));
                    card.append(variants);
                }
                if (editable) {
                    const tools = node("div", undefined, "schema-card__tools");
                    const visibility = iconButton(row.is_active ? "Скрыть вопрос" : "Показать вопрос", row.is_active ? "visibility" : "visibility_off", event => {
                        if (row.is_active) hideQuestion(row, event.currentTarget);
                        else {
                            row.is_active = true;
                            sync();
                            render();
                        }
                    });
                    visibility.dataset.questionVisibility = "";
                    tools.append(visibility,
                        iconButton("Дублировать вопрос", "content_copy", () => {
                            const copy = independent(row);
                            rows.splice(i + 1, 0, copy);
                            selected = copy;
                            sync();
                            render();
                        }),
                        iconButton("Удалить вопрос", "delete", event => {
                            if (row.field_type === "datefee") {
                                confirmAction(event.currentTarget, feeCancelText(), "Удалить блок и отменить плату", () => {
                                    if (row.id) acknowledge(String(row.id));
                                    rows.splice(rows.indexOf(row), 1);
                                    sync();
                                    render();
                                });
                                return;
                            }
                            const count = hiding.counts[String(row.id)] || 0;
                            if (count) confirmAction(event.currentTarget, `Есть ответы в ${countIn(count)}. Для сохранения ответов используйте скрытие вопроса.`);
                            else confirmAction(event.currentTarget, "Ответов в анкетах нет. Вопрос и его варианты будут удалены из шаблона после сохранения фестиваля.", "Удалить вопрос", () => {
                                rows.splice(rows.indexOf(row), 1);
                                sync();
                                render();
                            });
                        })
                    );
                    settings.append(tools);
                }
                card.append(settings);
                setExpanded();
                container.append(card);
            });
            root.querySelector("[data-schema-count]").textContent = `(${rows.length})`;
        };
        desktop.addEventListener("change", render);
        root.querySelectorAll("[data-expand-questions]").forEach(control => control.addEventListener("click", () => {
            rows.forEach(row => control.dataset.expandQuestions === "true" ? expanded.add(row) : expanded.delete(row));
            render();
        }));
        root.querySelector("[data-add-field]")?.addEventListener("click", () => {
            const row = {
                id: null,
                field_type: "text",
                label: "",
                help_text: "",
                is_required: false,
                in_application_name: false,
                is_active: true,
                options: [],
                services: []
            };
            rows.push(row);
            selected = row;
            expanded.add(row);
            sync();
            render();
            container.lastElementChild.querySelector("input")?.focus();
        });
        root.querySelector("[data-copy-template]")?.addEventListener("click", async (event) => {
            showMessage(copyMessage, "");
            const source = root.querySelector("[data-copy-source]").value;
            if (!source) {
                showMessage(copyMessage, "Выберите фестиваль-источник.");
                return;
            }
            const trigger = event.currentTarget;
            trigger.disabled = true;
            try {
                const response = await fetch(root.dataset.sourceUrl.replace("/0/", `/${source}/`), {
                    credentials: "same-origin",
                    headers: {
                        Accept: "application/json"
                    }
                });
                if (!response.ok) throw new Error("Шаблон недоступен. Текущие изменения сохранены в редакторе.");
                const data = await response.json();
                rows.push(...data.schema.map(independent));
                sync();
                render();
                showMessage(copyMessage, data.schema.length ? `Добавлено элементов: ${data.schema.length}. Проверьте даты, цены и подписи перед сохранением фестиваля.` : "В выбранном фестивале шаблон пуст.", false);
            } catch (error) {
                showMessage(copyMessage, error.message);
            } finally {
                trigger.disabled = false;
            }
        });
        const showPreview = async (validate = false) => {
            showMessage(previewMessage, "");
            const requestVersion = version;
            const requestPreviewVersion = ++previewVersion;
            const payload = {
                schema: rows,
                festival_id: Number(root.dataset.festival) || null,
                timezone: festivalForm.querySelector('[name="timezone"]').value
            };
            if (validate) {
                const answers = {};
                previewFrame.contentDocument.querySelectorAll("input, select, textarea").forEach(el => {
                    if (!el.name || ((el.type === "checkbox" || el.type === "radio") && !el.checked)) return;
                    if (el.type === "checkbox")(answers[el.name] ||= []).push(el.value);
                    else answers[el.name] = el.value;
                });
                payload.answers = answers;
            }
            try {
                const response = await fetch(root.dataset.previewUrl, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": document.querySelector('[name="csrfmiddlewaretoken"]').value
                    },
                    body: JSON.stringify(payload)
                });
                if (response.redirected) throw new Error("Сессия завершилась. Сохраните введённые данные и войдите заново.");
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || "Предпросмотр недоступен.");
                if (version !== requestVersion || previewVersion !== requestPreviewVersion) {
                    showMessage(previewMessage, "Данные изменены. Повторите предпросмотр или проверку.");
                    return;
                }
                previewFrame.srcdoc = data.html;
            } catch (error) {
                showMessage(previewMessage, error.message);
            }
        };
        const previewButton = root.querySelector("[data-show-preview]");
        previewButton.setAttribute("aria-controls", "schema-preview-dialog");
        previewButton.addEventListener("click", () => {
            dialog.showModal();
            document.body.style.overflow = "hidden";
            showPreview();
        });
        root.querySelector("[data-close-preview]").addEventListener("click", () => dialog.close());
        dialog.addEventListener("click", event => {
            if (event.target === dialog) {
                const r = dialog.getBoundingClientRect();
                if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
            }
        });
        dialog.addEventListener("close", () => {
            previewVersion += 1;
            document.body.style.overflow = "";
            previewButton.focus();
        });
        previewFrame.addEventListener("load", () => {
            const doc = previewFrame.contentDocument;
            doc.querySelector("[data-validate-preview]")?.addEventListener("click", () => showPreview(true));
            doc.addEventListener("keydown", event => {
                if (event.key === "Escape") dialog.close();
            });
            doc.addEventListener("input", () => {
                previewVersion += 1;
                doc.querySelector("[data-server-result]")?.remove();
            });
        });
        festivalForm?.addEventListener("submit", event => {
            for (const row of rows) {
                const pending = [];
                if (!row.is_active && hiding.active.includes(row.id) && !hiding.confirmed.includes(String(row.id))) pending.push(String(row.id));
                for (const [options, prefix] of [
                        [row.options || [], "a"],
                        [row.services || [], "s"]
                    ]) {
                    for (const option of options) {
                        const key = `${prefix}:${option.id}`;
                        if (!option.is_active && hiding.active_options.includes(key) && !hiding.confirmed.includes(key)) pending.push(key);
                    }
                }
                if (pending.length) {
                    event.preventDefault();
                    if (desktop.matches && selected !== row) {
                        selected = row;
                        render();
                    }
                    const trigger = container.querySelector(`[data-editor-id="${identity(row)}"] [data-question-visibility]`);
                    const total = pending.reduce((sum, key) => sum + (hiding.counts[key] || hiding.option_counts[key] || 0), 0);
                    confirmAction(trigger, `Подтвердите скрытие элементов. Затронутые анкеты и выборы: ${total}. Сохранённые данные останутся в анкетах.`, "Подтвердить и сохранить", () => {
                        pending.forEach(acknowledge);
                        festivalForm.requestSubmit(event.submitter);
                    });
                    return;
                }
            }
        });
        document.querySelector("[data-open-schema]")?.addEventListener("click", () => {
            root.open = true;
        });
        render();
        checkPublication();
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
