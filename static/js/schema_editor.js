(() => {
  "use strict";
  const init = () => {
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
      if (preview.childElementCount) {
        preview.replaceChildren(node("p", "Шаблон изменён. Обновите предпросмотр."));
      }
    };
    const button = (label, action, disabled = false) => {
      const el = node("button", label);
      el.type = "button";
      el.disabled = !editable || disabled;
      el.addEventListener("click", action);
      return el;
    };
    const control = (parent, object, name, title, type = "text", values = null) => {
      const label = node("label", type === "checkbox" ? undefined : title, type === "checkbox" ? "schema-check" : "schema-control");
      let el;
      if (values) {
        el = node("select");
        values.forEach(([value, caption]) => { const o = node("option", caption); o.value = value; el.append(o); });
      } else {
        el = node(type === "textarea" ? "textarea" : "input");
        if (type !== "textarea") el.type = type;
        else el.rows = 2;
        if (type === "number") { el.min = "0"; el.step = "0.01"; }
      }
      el.disabled = !editable;
      if (type === "checkbox") el.checked = Boolean(object[name]);
      else el.value = object[name] ?? "";
      el.addEventListener(type === "checkbox" || values ? "change" : "input", () => {
        object[name] = type === "checkbox" ? el.checked : el.value;
        sync();
      });
      label.append(el);
      if (type === "checkbox") label.append(node("span", title));
      parent.append(label);
      return el;
    };
    const move = (list, index, delta) => {
      [list[index], list[index + delta]] = [list[index + delta], list[index]];
      sync(); render();
    };
    const independent = (row) => {
      const copy = structuredClone(row);
      copy.id = null;
      [...(copy.options || []), ...(copy.services || [])].forEach(o => { o.id = null; });
      return copy;
    };
    const render = () => {
      container.replaceChildren();
      rows.forEach((row, i) => {
        row.options ||= [];
        row.services ||= [];
        const card = node("section", undefined, "schema-card");
        card.setAttribute("aria-label", `Элемент ${i + 1}`);
        const head = node("div", undefined, "schema-field-head");
        head.append(node("span", `${i + 1}`, "schema-index"));
        control(head, row, "label", "Название");
        const kind = control(head, row, "field_type", "Тип", "text", types);
        kind.addEventListener("change", () => {
          if (row.field_type === "info") { row.is_required = false; row.is_recommended = false; }
          sync(); render();
        });
        card.append(head);
        control(card, row, "help_text", row.field_type === "info" ? "Текст и ссылки" : "Пояснение", "textarea");
        const settings = node("div", undefined, "schema-settings");
        if (row.field_type !== "info") {
          const mode = {value: row.is_required ? "required" : row.is_recommended ? "recommended" : "optional"};
          const select = control(settings, mode, "value", "Заполнение", "text", [["optional", "Необязательное"], ["recommended", "Рекомендуется заполнить"], ["required", "Обязательное"]]);
          select.addEventListener("change", () => { row.is_required = mode.value === "required"; row.is_recommended = mode.value === "recommended"; sync(); });
        }
        control(settings, row, "is_active", "Активный элемент", "checkbox");
        card.append(settings);
        const priced = row.field_type === "service";
        if (priced || ["single", "multi"].includes(row.field_type)) {
          const list = priced ? row.services : row.options;
          const variants = node("div", undefined, "schema-options");
          variants.append(node("h3", priced ? "Варианты услуг" : "Варианты ответа"));
          list.forEach((option, j) => {
            const line = node("div", undefined, "schema-option");
            control(line, option, "label", "Подпись");
            if (priced) {
              control(line, option, "service_date", "Дата", "date");
              const price = control(line, option, "price", "Цена, ₽", "number");
              price.disabled ||= Boolean(option.is_opt_out);
              const refuse = control(line, option, "is_opt_out", "Отказ от услуг этого поля", "checkbox");
              refuse.addEventListener("change", () => { if (option.is_opt_out) option.price = "0.00"; sync(); render(); });
            }
            control(line, option, "is_active", "Активный вариант", "checkbox");
            const tools = node("div", undefined, "schema-item-actions");
            const remove = button("Удалить вариант", () => { list.splice(j, 1); sync(); render(); });
            remove.classList.add("schema-button--delete");
            tools.append(button("Вверх", () => move(list, j, -1), j === 0), button("Вниз", () => move(list, j, 1), j === list.length - 1), remove);
            line.append(tools);
            variants.append(line);
          });
          if (editable) variants.append(button("Добавить вариант", () => {
            list.push(priced ? {id: null, label: "", service_date: null, price: "0.00", is_opt_out: false, is_active: true} : {id: null, label: "", is_active: true});
            sync(); render();
          }));
          card.append(variants);
        }
        if (editable) {
          const tools = node("div", undefined, "schema-card-actions");
          const remove = button("Удалить", () => { rows.splice(i, 1); sync(); render(); });
          remove.classList.add("schema-button--delete");
          tools.append(button("Вверх", () => move(rows, i, -1), i === 0), button("Вниз", () => move(rows, i, 1), i === rows.length - 1), button("Дублировать", () => { rows.splice(i + 1, 0, independent(row)); sync(); render(); }), remove);
          card.append(tools);
        }
        container.append(card);
      });
      root.querySelector("[data-schema-count]").textContent = `(${rows.length})`;
    };
    root.querySelector("[data-add-field]")?.addEventListener("click", () => {
      rows.push({id: null, field_type: "text", label: "", help_text: "", is_required: false, is_recommended: false, is_active: true, options: [], services: []});
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
        if (changed && group.contains(changed) && changed.checked) {
          boxes.forEach(box => {
            if (box !== changed && (changed.dataset.optOut === "true" || box.dataset.optOut === "true")) box.checked = false;
          });
        }
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
    root.querySelector("[data-show-preview]").addEventListener("click", () => showPreview());
    preview.addEventListener("click", event => { if (event.target.closest("[data-validate-preview]")) showPreview(true); });
    preview.addEventListener("change", event => totals(event.target));
    preview.addEventListener("input", () => { previewVersion += 1; preview.querySelector("[data-server-result]")?.remove(); });
    document.querySelector("[data-open-schema]")?.addEventListener("click", () => { root.open = true; });
    render();
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
