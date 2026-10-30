(() => {
  "use strict";
  const init = () => {
    const publish = document.querySelector("[data-publish]");
    const publication = document.querySelector("[data-publication-url]");
    const festivalForm = document.getElementById("festival_form");
    let checkVersion = 0;
    let timer;
    const reasons = document.getElementById("publish-reasons");
    const checkPublication = async () => {
      const current = ++checkVersion;
      if (!publish || !publication) return;
      publish.disabled = true;
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
        publish.disabled = !data.ready;
        reasons.textContent = data.errors.join("; ");
      } catch {
        if (current === checkVersion) reasons.textContent = "Не удалось проверить готовность. Сохраните черновик и обновите страницу.";
      }
    };
    const scheduleCheck = () => {
      if (!publish) return;
      ++checkVersion;
      publish.disabled = true;
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
    const control = (parent, object, name, title, type = "text", values = null) => {
      const label = node("label", type === "checkbox" ? undefined : title, type === "checkbox" ? "schema-check" : "schema-control text-subtle");
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
      if (from < 0 || from === position) return;
      list.splice(position, 0, list.splice(from, 1)[0]);
      sync();
      requestAnimationFrame(render);
    };
    window.riSchemaSort = (id, position) => { if (editable) reorder(rows, id, position); };
    window.riSchemaOptionSort = (owner, id, position) => {
      const row = rows.find(item => identity(item) === owner);
      if (editable && row) reorder(row.field_type === "service" ? row.services : row.options, id, position);
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
        if (to < 0 || to >= list.length) return;
        list.splice(to, 0, list.splice(from, 1)[0]);
        sync(); render();
        container.querySelector(`[data-editor-id="${identity(object)}"] [data-drag-handle]`)?.focus();
        message.textContent = `Новая позиция: ${to + 1}.`;
      });
      return el;
    };
    const render = () => {
      container.replaceChildren();
      rows.forEach((row, i) => {
        row.options ||= [];
        row.services ||= [];
        const rowId = identity(row);
        const card = node("section", undefined, "schema-card admin-panel admin-panel--muted");
        if (row.field_type === "info") card.classList.add("schema-card--info");
        card.setAttribute("aria-label", `Вопрос ${i + 1}`);
        card.dataset.number = String(i + 1);
        card.setAttribute("x-sort:item", JSON.stringify(rowId));
        card.dataset.editorId = rowId;
        const head = node("div", undefined, "schema-card-head");
        if (editable) head.append(handle(rows, row));
        control(head, row, "label", "Название");
        const kind = control(head, row, "field_type", "Тип", "text", types);
        kind.addEventListener("change", () => {
          if (row.field_type === "info") row.is_required = false;
          sync(); render();
        });
        card.append(head);
        control(card, row, "help_text", row.field_type === "info" ? "Текст и ссылки" : "Пояснение", "textarea");
        const settings = node("div", undefined, "schema-flags");
        if (row.field_type !== "info") control(settings, row, "is_required", "Обязательно", "checkbox");
        if (!row.is_active) settings.append(node("span", "Скрыт от участников", "schema-hidden-note"));
        const priced = row.field_type === "service";
        if (priced || ["single", "multi"].includes(row.field_type)) {
          const list = priced ? row.services : row.options;
          const variants = node("div", undefined, "schema-options");
          variants.append(node("h3", priced ? "Варианты услуг" : "Варианты ответа", "font-semibold"));
          const sortable = node("div");
          if (editable) {
            sortable.setAttribute("x-sort", `window.riSchemaOptionSort('${rowId}', $item, $position)`);
            sortable.setAttribute("x-sort:config", "{handle: '[data-drag-handle]', animation: 150}");
          }
          list.forEach((option, j) => {
            const line = node("div", undefined, "schema-option");
            line.setAttribute("x-sort:item", JSON.stringify(identity(option)));
            line.dataset.editorId = identity(option);
            if (editable) line.append(handle(list, option));
            control(line, option, "label", "Подпись");
            if (priced) {
              control(line, option, "service_date", "Дата", "date");
              control(line, option, "price", "Цена, ₽", "number");
            }
            if (!option.is_active) line.append(node("span", "Скрыт", "schema-hidden-note"));
            if (editable) {
              line.append(iconButton(option.is_active ? "Скрыть вариант" : "Показать вариант", option.is_active ? "visibility_off" : "visibility", () => { option.is_active = !option.is_active; sync(); render(); }));
              line.append(iconButton("Удалить вариант", "delete", () => { list.splice(j, 1); sync(); render(); }));
            }
            sortable.append(line);
          });
          variants.append(sortable);
          if (editable) variants.append(button("Добавить вариант", () => {
            list.push(priced ? {id: null, label: "", service_date: null, price: "0.00", is_active: true} : {id: null, label: "", is_active: true});
            sync(); render();
          }));
          card.append(variants);
        }
        if (editable) {
          const tools = node("div", undefined, "schema-card-tools");
          tools.append(
            iconButton(row.is_active ? "Скрыть вопрос" : "Показать вопрос", row.is_active ? "visibility" : "visibility_off", () => { row.is_active = !row.is_active; sync(); render(); }),
            iconButton("Дублировать вопрос", "content_copy", () => { rows.splice(i + 1, 0, independent(row)); sync(); render(); }),
            iconButton("Удалить вопрос", "delete", () => { rows.splice(i, 1); sync(); render(); })
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
        message.textContent = data.schema.length ? `Добавлено элементов: ${data.schema.length}. Проверьте даты и цены перед сохранением.` : "В выбранном фестивале шаблон пуст.";
      } catch (error) { message.textContent = error.message; }
      finally { trigger.disabled = false; }
    });
    const money = (kopecks) => `${(kopecks / 100n).toLocaleString("ru-RU")},${(kopecks % 100n).toString().padStart(2, "0")} ₽`;
    const totals = (changed = null) => {
      let total = 0n;
      preview.querySelectorAll("[data-service-group]").forEach(group => {
        const boxes = [...group.querySelectorAll("input[type=checkbox]")];
        const value = boxes.filter(box => box.checked).reduce((sum, box) => sum + BigInt(box.dataset.kopecks), 0n);
        group.querySelector("[data-group-total]").textContent = money(value);
        total += value;
      });
      const output = preview.querySelector("[data-total-services]");
      if (output) output.textContent = money(total);
      if (changed) preview.querySelector("[data-server-result]")?.remove();
    };
    const showPreview = async (validate = false) => {
      const requestVersion = version;
      const requestPreviewVersion = ++previewVersion;
      const payload = {schema: rows, festival_id: Number(root.dataset.festival) || null};
      if (validate) {
        const answers = {};
        preview.querySelectorAll("input, select, textarea").forEach(el => {
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
        preview.innerHTML = data.html;
        totals();
      } catch (error) { message.textContent = error.message; }
    };
    const previewButton = root.querySelector("[data-show-preview]");
    previewButton.setAttribute("aria-controls", "schema-preview");
    previewButton.setAttribute("aria-expanded", "false");
    previewButton.addEventListener("click", () => {
      if (!preview.hidden) {
        preview.hidden = true;
        previewVersion += 1;
        previewButton.setAttribute("aria-expanded", "false");
        previewButton.textContent = "Предпросмотр анкеты";
      } else {
        preview.hidden = false;
        previewButton.setAttribute("aria-expanded", "true");
        previewButton.textContent = "Скрыть предпросмотр";
        showPreview();
      }
    });
    preview.addEventListener("click", event => { if (event.target.closest("[data-validate-preview]")) showPreview(true); });
    preview.addEventListener("change", event => totals(event.target));
    preview.addEventListener("input", () => { previewVersion += 1; preview.querySelector("[data-server-result]")?.remove(); });
    document.querySelector("[data-open-schema]")?.addEventListener("click", () => { root.open = true; });
    render();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
