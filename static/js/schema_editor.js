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
          method: "POST", credentials: "same-origin",
          headers: {"Content-Type": "application/json", "X-CSRFToken": payload.csrfmiddlewaretoken},
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
    const preview = root.querySelector("[data-schema-preview]");
    const dialog = document.getElementById("schema-preview-dialog");
    const previewFrame = dialog.querySelector("iframe");
    const hidingInput = root.querySelector('[name="schema_hiding"]');
    let hiding = {counts: {}, option_counts: {}, active: [], active_options: [], confirmed: []};
    try { hiding = {...hiding, ...JSON.parse(hidingInput.value || "{}")}; } catch { /* Server rechecks confirmation. */ }
    const message = root.querySelector("[data-schema-message]");
    let rows;
    try { rows = JSON.parse(input.value || "[]") || []; }
    catch { rows = []; message.textContent = "Некорректные данные шаблона. Исправьте ошибку перед сохранением."; }
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
      const {panel, trigger} = confirmation;
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
      const actions = node("div", undefined, "schema-confirm-actions");
      const cancel = button(action ? "Отмена" : "Закрыть", () => closeConfirmation());
      actions.append(cancel);
      if (action) actions.append(button(caption, () => {
        const owner = trigger.closest("[data-editor-id]")?.dataset.editorId;
        const group = trigger.parentElement.classList.contains("schema-option-actions") ? ".schema-option-actions" : ".schema-card-tools";
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
      confirmation = {panel, trigger};
      cancel.focus();
    };
    document.addEventListener("pointerdown", event => {
      if (confirmation && !confirmation.panel.contains(event.target) && !confirmation.trigger.contains(event.target)) closeConfirmation(false);
    });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && confirmation) { event.preventDefault(); closeConfirmation(); }
    });
    const acknowledge = key => {
      if (!hiding.confirmed.includes(key)) hiding.confirmed.push(key);
      hidingInput.value = JSON.stringify(hiding);
    };
    const questionHideText = row => `Анкет с ответами: ${hiding.counts[String(row.id)] || 0}. Вопрос и сохранённые ответы будут скрыты от участников. В новых анкетах вопрос не появится; данные сохранятся.`;
    const hideQuestion = (row, trigger) => confirmAction(trigger, questionHideText(row), "Скрыть вопрос", () => {
      if (row.id) acknowledge(String(row.id));
      row.is_active = false; sync(); render();
    });
    const control = (parent, object, name, title, type = "text", values = null) => {
      const label = node("label", type === "checkbox" ? undefined : title, type === "checkbox" ? "schema-check" : "schema-control text-subtle");
      label.dataset.control = name;
      const widget = root.querySelector(`[data-editor-control="${values ? "select" : type}"]`).content.cloneNode(true);
      const el = widget.querySelector("input, select, textarea");
      el.removeAttribute("name");
      el.removeAttribute("id");
      el.required = false;
      if (values) {
        el.replaceChildren();
        values.forEach(([value, caption]) => { const o = node("option", caption); o.value = value; el.append(o); });
      } else {
        if (type !== "textarea") el.type = type;
        else el.rows = 1;
        if (type === "number") { el.min = "0"; el.step = "0.01"; }
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
      [...(copy.options || []), ...(copy.services || [])].forEach(o => { o.id = null; });
      return copy;
    };
    const ids = new WeakMap();
    let serial = 0;
    const identity = object => {
      if (!ids.has(object)) ids.set(object, `editor-${++serial}`);
      return ids.get(object);
    };
    const iconButton = (label, icon, action) => {
      const el = button("", action);
      el.replaceChildren(node("span", icon, "material-symbols-outlined"));
      el.classList.add("schema-editor-icon");
      el.setAttribute("aria-label", label);
      el.title = label;
      return el;
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
    window.riSchemaSort = (id, position) => { if (editable) reorder(rows, id, position); };
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
        sync(); render();
        container.querySelector(`[data-editor-id="${identity(object)}"] [data-drag-handle]`)?.focus();
        message.textContent = `Новая позиция: ${to + 1}.`;
      });
      return el;
    };
    const render = () => {
      closeConfirmation(false);
      container.replaceChildren();
      let questionNumber = 0;
      rows.forEach((row, i) => {
        row.options ||= [];
        row.services ||= [];
        const rowId = identity(row);
        const card = node("section", undefined, "schema-card admin-panel admin-panel--muted");
        if (!row.is_active) card.classList.add("schema-is-hidden");
        if (row.field_type === "info") card.classList.add("schema-card--info");
        if (row.field_type !== "info") questionNumber += 1;
        card.setAttribute("aria-label", row.field_type === "info" ? "Текстовый блок" : `Вопрос ${questionNumber}`);
        card.dataset.number = row.field_type === "info" ? "" : String(questionNumber);
        card.setAttribute("x-sort:item", JSON.stringify(rowId));
        card.dataset.editorId = rowId;
        const head = node("div", undefined, "schema-card-head");
        if (editable) head.append(handle(rows, row));
        control(head, row, "label", "Название");
        const originalKind = row.field_type;
        const kind = control(head, row, "field_type", "Тип", "text", types);
        kind.addEventListener("change", () => {
          if ((hiding.counts[String(row.id)] || 0) > 0 && row.field_type !== originalKind) {
            message.textContent = `Вопрос заполнен в ${countIn(hiding.counts[String(row.id)])}. Чтобы использовать другой тип, добавьте новый вопрос.`;
            row.field_type = originalKind;
          }
          if (row.field_type === "info") row.is_required = false;
          sync(); render();
        });
        card.append(head);
        control(card, row, "help_text", row.field_type === "info" ? "Текст и ссылки" : "Пояснение", "textarea");
        const settings = node("div", undefined, "schema-flags");
        if (row.field_type !== "info") control(settings, row, "is_required", "Обязательно", "checkbox");
        if (!row.is_active) settings.append(node("span", "Скрыт от участников", "schema-hidden-note"));
        const priced = ["service", "service1"].includes(row.field_type);
        if (priced || ["single", "multi"].includes(row.field_type)) {
          const list = priced ? row.services : row.options;
          if (priced) list.sort((a, b) => Number(Boolean(a.is_opt_out)) - Number(Boolean(b.is_opt_out)));
          const variants = node("div", undefined, "schema-options");
          variants.append(node("h3", priced ? "Варианты услуг" : "Варианты ответа", "font-semibold"));
          const sortable = node("div");
          if (editable) {
            sortable.setAttribute("x-sort", `window.riSchemaOptionSort('${rowId}', $item, $position)`);
            sortable.setAttribute("x-sort:config", "{handle: '[data-drag-handle]', animation: 150}");
          }
          list.forEach((option, j) => {
            const optionCount = hiding.option_counts?.[`${priced ? "s" : "a"}:${option.id}`] || 0;
            const line = node("div", undefined, "schema-option");
            line.classList.add(priced ? "schema-option--service" : "schema-option--choice");
            if (!option.is_opt_out) line.setAttribute("x-sort:item", JSON.stringify(identity(option)));
            else line.classList.add("schema-option--refusal");
            line.dataset.editorId = identity(option);
            if (editable && !option.is_opt_out) line.append(handle(list, option));
            control(line, option, "label", "Подпись");
            if (priced) {
              if (option.is_opt_out) {
                line.append(node("span", "Отказ от услуги · бесплатно", "schema-hidden-note schema-refusal-note"));
              } else {
                control(line, option, "service_date", "Дата", "date");
                control(line, option, "price", "Цена, ₽", "number");
              }
            }
            if (!option.is_active) { line.classList.add("schema-is-hidden"); line.append(node("span", "Скрыт", "schema-hidden-note")); }
            if (editable) {
              const tools = node("div", undefined, "schema-option-actions");
              const key = `${priced ? "s" : "a"}:${option.id}`;
              const hideOption = trigger => confirmAction(trigger,
                `Вариант выбран в ${countIn(optionCount)}. ` + (priced
                  ? "Сохранённые выборы и их стоимость останутся в анкетах. Для нового выбора услуга будет скрыта."
                  : "Сохранённый выбор останется в ответах; при следующей полной проверке анкеты потребуется выбрать доступный вариант."),
                "Скрыть вариант", () => { if (option.id) acknowledge(key); option.is_active = false; sync(); render(); });
              const visibility = iconButton(option.is_active ? "Скрыть вариант" : "Показать вариант", option.is_active ? "visibility" : "visibility_off", event => {
                if (option.is_active) hideOption(event.currentTarget);
                else { option.is_active = true; sync(); render(); }
              });
              tools.append(visibility, iconButton("Удалить вариант", "delete", event => {
                if (optionCount) confirmAction(event.currentTarget, `Вариант выбран в ${countIn(optionCount)}. Для сохранения этих данных используйте скрытие варианта.`,
                  option.is_active ? "Скрыть вариант" : null, option.is_active ? () => hideOption(visibility) : null);
                else confirmAction(event.currentTarget, "Вариант не выбран ни в одной анкете. После сохранения фестиваля он будет удалён из шаблона.", "Удалить вариант", () => { list.splice(list.indexOf(option), 1); sync(); render(); });
              }));
              line.append(tools);
            }
            sortable.append(line);
          });
          variants.append(sortable);
          if (editable) variants.append(button("Добавить вариант", () => {
            list.push(priced ? {id: null, label: "", service_date: null, price: "0.00", is_active: true} : {id: null, label: "", is_active: true});
            sync(); render();
          }));
          if (editable && priced && !list.some(option => option.is_opt_out)) variants.append(button("Добавить отказ от услуги", () => {
            list.push({id: null, label: "", service_date: null, price: "0.00", is_active: true, is_opt_out: true});
            sync(); render();
          }));
          card.append(variants);
        }
        if (editable) {
          const tools = node("div", undefined, "schema-card-tools");
          const visibility = iconButton(row.is_active ? "Скрыть вопрос" : "Показать вопрос", row.is_active ? "visibility" : "visibility_off", event => {
            if (row.is_active) hideQuestion(row, event.currentTarget);
            else { row.is_active = true; sync(); render(); }
          });
          visibility.dataset.questionVisibility = "";
          tools.append(visibility,
            iconButton("Дублировать вопрос", "content_copy", () => { rows.splice(i + 1, 0, independent(row)); sync(); render(); }),
            iconButton("Удалить вопрос", "delete", event => {
              const count = hiding.counts[String(row.id)] || 0;
              if (count) confirmAction(event.currentTarget, `Есть ответы в ${countIn(count)}. Для сохранения ответов используйте скрытие вопроса.`,
                row.is_active ? "Скрыть вопрос" : null, row.is_active ? () => hideQuestion(row, visibility) : null);
              else confirmAction(event.currentTarget, "Ответов в анкетах нет. Вопрос и его варианты будут удалены из шаблона после сохранения фестиваля.", "Удалить вопрос", () => { rows.splice(rows.indexOf(row), 1); sync(); render(); });
            })
          );
          settings.append(tools);
        }
        card.append(settings);
        container.append(card);
      });
      root.querySelector("[data-schema-count]").textContent = `(${rows.length})`;
    };
    root.querySelector("[data-add-field]")?.addEventListener("click", () => {
      rows.push({id: null, field_type: "text", label: "", help_text: "", is_required: false, is_active: true, options: [], services: []});
      sync(); render();
      container.lastElementChild.querySelector("input")?.focus();
    });
    root.querySelector("[data-copy-template]")?.addEventListener("click", async (event) => {
      const source = root.querySelector("[data-copy-source]").value;
      if (!source) { message.textContent = "Выберите фестиваль-источник."; return; }
      const trigger = event.currentTarget;
      trigger.disabled = true;
      try {
        const response = await fetch(root.dataset.sourceUrl.replace("/0/", `/${source}/`), {credentials: "same-origin", headers: {Accept: "application/json"}});
        if (!response.ok) throw new Error("Шаблон недоступен. Текущие изменения сохранены в редакторе.");
        const data = await response.json();
        rows.push(...data.schema.map(independent));
        sync(); render();
        message.textContent = data.schema.length ? `Добавлено элементов: ${data.schema.length}. Проверьте даты, цены и подписи перед сохранением фестиваля.` : "В выбранном фестивале шаблон пуст.";
      } catch (error) { message.textContent = error.message; }
      finally { trigger.disabled = false; }
    });
    const showPreview = async (validate = false) => {
      const requestVersion = version;
      const requestPreviewVersion = ++previewVersion;
      const payload = {schema: rows, festival_id: Number(root.dataset.festival) || null};
      if (validate) {
        const answers = {};
        previewFrame.contentDocument.querySelectorAll("input, select, textarea").forEach(el => {
          if (!el.name || ((el.type === "checkbox" || el.type === "radio") && !el.checked)) return;
          if (el.type === "checkbox") (answers[el.name] ||= []).push(el.value);
          else answers[el.name] = el.value;
        });
        payload.answers = answers;
      }
      try {
        const response = await fetch(root.dataset.previewUrl, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json", "X-CSRFToken": document.querySelector('[name="csrfmiddlewaretoken"]').value}, body: JSON.stringify(payload)});
        if (response.redirected) throw new Error("Сессия завершилась. Сохраните введённые данные и войдите заново.");
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Предпросмотр недоступен.");
        if (version !== requestVersion || previewVersion !== requestPreviewVersion) { message.textContent = "Данные изменены. Повторите предпросмотр или проверку."; return; }
        previewFrame.srcdoc = data.html;
      } catch (error) { message.textContent = error.message; }
    };
    const previewButton = root.querySelector("[data-show-preview]");
    previewButton.setAttribute("aria-controls", "schema-preview-dialog");
    previewButton.addEventListener("click", () => { dialog.showModal(); document.body.style.overflow = "hidden"; showPreview(); });
    root.querySelector("[data-close-preview]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); } });
    dialog.addEventListener("close", () => { previewVersion += 1; document.body.style.overflow = ""; previewButton.focus(); });
    previewFrame.addEventListener("load", () => {
      const doc = previewFrame.contentDocument;
      doc.querySelector("[data-validate-preview]")?.addEventListener("click", () => showPreview(true));
      doc.addEventListener("keydown", event => { if (event.key === "Escape") dialog.close(); });
      doc.addEventListener("input", () => { previewVersion += 1; doc.querySelector("[data-server-result]")?.remove(); });
    });
    festivalForm?.addEventListener("submit", event => {
      for (const row of rows) {
        const pending = [];
        if (!row.is_active && hiding.active.includes(row.id) && !hiding.confirmed.includes(String(row.id))) pending.push(String(row.id));
        for (const [options, prefix] of [[row.options || [], "a"], [row.services || [], "s"]]) {
          for (const option of options) {
            const key = `${prefix}:${option.id}`;
            if (!option.is_active && hiding.active_options.includes(key) && !hiding.confirmed.includes(key)) pending.push(key);
          }
        }
        if (pending.length) {
          event.preventDefault();
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
    document.querySelector("[data-open-schema]")?.addEventListener("click", () => { root.open = true; });
    render();
    checkPublication();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
