(() => {
    "use strict";
    const payment = document.querySelector("[data-payment-form]");
    if (!payment) return;
    const endpoint = new URL(payment.getAttribute("action") || location.href, location.href);
    const panel = payment.closest("[data-payment-panel]");
    const notice = payment.querySelector("[data-payment-status]");
    const refresh = payment.querySelector("[data-payment-refresh]");
    const amount = payment.elements.amount;
    const buttons = payment.querySelectorAll("button[type='submit']");
    const answerVersion = document.querySelector("[data-application-form] input[name='version']");
    const answerButtons = document.querySelectorAll("button[form='application_form']");
    const answerDisabled = Array.from(answerButtons, button => button.disabled);
    let busy = false;
    let uncertain = false;
    let canChange = true;
    const update = () => {
        payment.setAttribute("aria-busy", String(busy));
        amount.disabled = busy || !canChange;
        buttons.forEach(button => {
            button.disabled = busy || uncertain || !canChange;
        });
        answerButtons.forEach((button, index) => {
            button.disabled = busy || answerDisabled[index];
        });
        refresh.hidden = !uncertain;
        refresh.disabled = busy;
    };
    const show = messages => {
        notice.replaceChildren(...messages.filter(Boolean).map(message => {
            const line = document.createElement("p");
            line.textContent = message;
            return line;
        }));
    };
    const send = async (url, options) => {
        busy = true;
        update();
        try {
            const response = await fetch(url, {
                ...options,
                headers: {
                    Accept: "application/json"
                },
                credentials: "same-origin",
                cache: "no-store",
                signal: AbortSignal.timeout(15000)
            });
            if (response.redirected || !response.headers.get("Content-Type")?.includes("application/json")) {
                throw new Error("Unexpected response");
            }
            const data = await response.json();
            if (!Array.isArray(data.errors) || typeof data.can_change_payment !== "boolean") {
                throw new Error("Invalid payment response");
            }
            if (data.amount_display !== undefined) {
                panel.querySelector("[data-payment-amount]").textContent = data.amount_display;
                payment.elements.payment_version.value = data.payment_version;
            }
            if (data.answer_version && answerVersion) answerVersion.value = data.answer_version;
            canChange = data.can_change_payment;
            uncertain = false;
            show([
                ...data.errors,
                data.message,
                data.warning,
                !canChange && "Изменение оплаты этой анкеты недоступно."
            ]);
            if (response.ok && !data.errors.length && options.method === "POST") amount.value = "";
            if (response.ok && options.method === "GET") {
                show([
                    "Актуальная оплата загружена.",
                    answerVersion && "Перед сохранением ответов проверьте, не изменились ли данные анкеты в другом окне.",
                    !canChange && "Изменение оплаты этой анкеты недоступно."
                ]);
            }
        } catch {
            uncertain = true;
            show(["Не удалось подтвердить результат. Обновите оплату перед повторной операцией: изменение могло уже сохраниться."]);
        } finally {
            busy = false;
            update();
            if (!uncertain && canChange) amount.focus({
                preventScroll: true
            });
        }
    };
    payment.addEventListener("submit", event => {
        event.preventDefault();
        if (busy || uncertain || !canChange) return;
        const data = new FormData(payment);
        data.set("payment_action", event.submitter?.value || "set");
        if (answerVersion) data.set("answers_version", answerVersion.value);
        send(endpoint, {
            method: "POST",
            body: data
        });
    });
    refresh.addEventListener("click", () => {
        if (busy) return;
        const url = new URL(endpoint);
        url.searchParams.set("payment_status", "1");
        send(url, {
            method: "GET"
        });
    });
    update();
})();
