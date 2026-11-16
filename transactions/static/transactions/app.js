/*
 * Small enhancements on top of the normal pages. Everything still works without
 * JavaScript: links open full pages and forms submit normally.
 *
 * - "+ New" menu (header button, or the round button on phones)
 * - Forms open in a sliding panel, and after saving the dashboard updates in place
 * - Show "number of installments" only when "Repeats" is "Installments"
 */
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);

  /* ---------- Installments field ---------- */

  function initForms(root) {
    root.querySelectorAll("[data-search-wrap]").forEach((wrap) => { wrap.hidden = false; }); // needs JavaScript
    initChartCards(root); // before initSegmented: it reveals the Month | Trend switch
    initSegmented(root);
    root.querySelectorAll('input[type="date"]').forEach(initDatePicker);
    root.querySelectorAll("[data-dropzone]").forEach(initDropzone);
    root.querySelectorAll('select[name="frequency"]').forEach((select) => {
      const field = select.form && select.form.querySelector(".field-installment_total");
      if (!field) return;
      const sync = () => { field.hidden = select.value !== "installments"; };
      select.addEventListener("change", sync);
      sync();
    });
  }
  /* ---------- Date picker ---------- */
  //
  // The browser's own calendar popup can't be styled, so date fields get our own.
  // The real <input name="date"> stays in the form (now hidden) and keeps the
  // value as YYYY-MM-DD, so the server sees exactly the same thing as before.

  const LANG = document.documentElement.lang || "en";
  const TEXT = document.documentElement.dataset;
  const fmtDisplay = new Intl.DateTimeFormat(LANG, { day: "numeric", month: "short", year: "numeric" });
  const fmtTitle = new Intl.DateTimeFormat(LANG, { month: "long", year: "numeric" });
  const fmtFull = new Intl.DateTimeFormat(LANG, { dateStyle: "full" });
  const fmtWeekdayShort = new Intl.DateTimeFormat(LANG, { weekday: "narrow" });
  const fmtWeekdayLong = new Intl.DateTimeFormat(LANG, { weekday: "long" });

  const pad = (n) => String(n).padStart(2, "0");
  const toISO = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const fromISO = (s) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || "");
    return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
  };
  const sameDay = (a, b) => a && b && toISO(a) === toISO(b);
  const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  const addMonthsKeepDay = (d, n) => {
    const last = new Date(d.getFullYear(), d.getMonth() + n + 1, 0).getDate();
    return new Date(d.getFullYear(), d.getMonth() + n, Math.min(d.getDate(), last));
  };
  const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  // First day of the week: Sunday in the US and Brazil, Monday in most Spanish-speaking countries.
  const WEEK_START = (() => {
    try {
      const locale = new Intl.Locale(LANG);
      const info = locale.getWeekInfo?.() ?? locale.weekInfo;
      if (info) return info.firstDay % 7;
    } catch { /* older browsers */ }
    return LANG.startsWith("es") ? 1 : 0;
  })();

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    Object.entries(props).forEach(([key, value]) => {
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("aria-") || key.startsWith("data-") || key === "role" || key === "tabindex") node.setAttribute(key, value);
      else node[key] = value;
    });
    children.forEach((child) => node.append(child));
    return node;
  }

  const CALENDAR_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/></svg>';
  const CHEVRON = (dir) => `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${dir === "prev" ? "M10 3.5 5.5 8l4.5 4.5" : "M6 3.5 10.5 8 6 12.5"}"/></svg>`;

  let closeOpenPicker = null;

  function initDatePicker(input) {
    if (input.dataset.enhanced) return;
    input.dataset.enhanced = "1";

    const wrapper = el("div", { class: "datepicker-field" });
    const button = el("button", { type: "button", class: "date-display", "aria-haspopup": "dialog", "aria-expanded": "false" });
    button.innerHTML = `<span class="date-display-text"></span>${CALENDAR_ICON}`;
    // The <label for="…"> should now point at our button.
    button.id = input.id;
    input.id = `${input.id}_value`;
    input.parentNode.insertBefore(wrapper, input);
    input.type = "hidden";
    wrapper.append(button, input);

    const refresh = () => {
      const date = fromISO(input.value);
      button.querySelector(".date-display-text").textContent = date ? fmtDisplay.format(date) : TEXT.i18nChooseDate;
      button.classList.toggle("is-empty", !date);
    };
    refresh();
    button.addEventListener("click", () => {
      if (closeOpenPicker && button.getAttribute("aria-expanded") === "true") closeOpenPicker();
      else openDatePicker(wrapper, input, button, refresh);
    });
  }

  function openDatePicker(wrapper, input, button, refresh) {
    closeOpenPicker?.(false);
    const today = new Date();
    const selected = fromISO(input.value);
    let focused = selected || today;
    let view = new Date(focused.getFullYear(), focused.getMonth(), 1);

    const popup = el("div", { class: "datepicker", role: "dialog", "aria-label": TEXT.i18nChooseDate });
    const title = el("span", { class: "dp-title", "aria-live": "polite" });
    const prev = el("button", { type: "button", class: "dp-nav", "aria-label": TEXT.i18nPrevMonth });
    const next = el("button", { type: "button", class: "dp-nav", "aria-label": TEXT.i18nNextMonth });
    prev.innerHTML = CHEVRON("prev");
    next.innerHTML = CHEVRON("next");
    const grid = el("div", { class: "dp-grid", role: "grid" });
    const todayButton = el("button", { type: "button", class: "dp-today", text: TEXT.i18nToday });
    popup.append(
      el("div", { class: "dp-header" }, [prev, title, next]),
      grid,
      el("div", { class: "dp-footer" }, [todayButton]),
    );

    function render(direction = 0, moveFocus = false) {
      title.textContent = capitalize(fmtTitle.format(view));
      grid.replaceChildren();
      // Weekday names, starting on the right day.
      for (let i = 0; i < 7; i += 1) {
        const day = new Date(2024, 0, 7 + ((WEEK_START + i) % 7)); // 7 Jan 2024 was a Sunday
        grid.append(el("span", { class: "dp-weekday", role: "columnheader", "aria-label": fmtWeekdayLong.format(day), text: fmtWeekdayShort.format(day) }));
      }
      // Six weeks, so the popup never changes height between months.
      const offset = (view.getDay() - WEEK_START + 7) % 7;
      const start = addDays(view, -offset);
      for (let i = 0; i < 42; i += 1) {
        const date = addDays(start, i);
        const classes = ["dp-day"];
        if (date.getMonth() !== view.getMonth()) classes.push("is-outside");
        if (sameDay(date, today)) classes.push("is-today");
        if (sameDay(date, selected)) classes.push("is-selected");
        grid.append(el("button", {
          type: "button",
          class: classes.join(" "),
          text: String(date.getDate()),
          "data-date": toISO(date),
          "aria-label": fmtFull.format(date),
          "aria-pressed": String(sameDay(date, selected)),
          tabindex: sameDay(date, focused) ? "0" : "-1",
        }));
      }
      if (direction && !reducedMotion()) {
        grid.classList.remove("slide-next", "slide-prev");
        grid.offsetWidth;
        grid.classList.add(direction > 0 ? "slide-next" : "slide-prev");
      }
      if (moveFocus) grid.querySelector('[tabindex="0"]')?.focus();
    }

    function goToMonth(delta, moveFocus = false) {
      view = new Date(view.getFullYear(), view.getMonth() + delta, 1);
      focused = addMonthsKeepDay(focused, delta);
      render(delta, moveFocus);
    }

    function pick(date) {
      input.value = toISO(date);
      refresh();
      input.dispatchEvent(new Event("change", { bubbles: true }));
      close();
    }

    function close(returnFocus = true) {
      document.removeEventListener("pointerdown", onOutside, true);
      button.setAttribute("aria-expanded", "false");
      closeOpenPicker = null;
      popup.classList.add("is-leaving");
      setTimeout(() => popup.remove(), reducedMotion() ? 0 : 140);
      if (returnFocus) button.focus();
    }

    function onOutside(event) {
      if (!wrapper.contains(event.target)) close(false);
    }

    prev.addEventListener("click", () => goToMonth(-1));
    next.addEventListener("click", () => goToMonth(1));
    todayButton.addEventListener("click", () => pick(new Date()));
    grid.addEventListener("click", (event) => {
      const day = event.target.closest(".dp-day");
      if (day) pick(fromISO(day.dataset.date));
    });
    popup.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation(); // don't also close the side panel
        close();
        return;
      }
      if (!event.target.classList.contains("dp-day")) return;
      const moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
      let target = null;
      if (event.key in moves) target = addDays(focused, moves[event.key]);
      else if (event.key === "PageUp") target = addMonthsKeepDay(focused, -1);
      else if (event.key === "PageDown") target = addMonthsKeepDay(focused, 1);
      else if (event.key === "Home") target = addDays(focused, -((focused.getDay() - WEEK_START + 7) % 7));
      else if (event.key === "End") target = addDays(focused, 6 - ((focused.getDay() - WEEK_START + 7) % 7));
      if (!target) return;
      event.preventDefault();
      const delta = (target.getFullYear() - view.getFullYear()) * 12 + target.getMonth() - view.getMonth();
      focused = target;
      if (delta) {
        view = new Date(target.getFullYear(), target.getMonth(), 1);
        render(delta, true);
      } else {
        render(0, true);
      }
    });

    render();
    wrapper.append(popup);
    button.setAttribute("aria-expanded", "true");
    // Open upwards when there's no room below (e.g. near the bottom of the screen).
    const box = wrapper.getBoundingClientRect();
    if (window.innerHeight - box.bottom < popup.offsetHeight + 16 && box.top > popup.offsetHeight + 16) {
      popup.classList.add("is-above");
    }
    grid.querySelector('[tabindex="0"]')?.focus({ preventScroll: true });
    document.addEventListener("pointerdown", onOutside, true);
    closeOpenPicker = close;
  }

  /* ---------- File drop area ---------- */

  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function initDropzone(zone) {
    const input = zone.querySelector('input[type="file"]');
    const error = zone.querySelector("[data-file-error]");
    if (!input || zone.dataset.ready) return;
    zone.dataset.ready = "1";
    const allowed = /\.(csv|pdf|txt)$/i;

    const show = () => {
      const file = input.files[0];
      zone.classList.toggle("has-file", Boolean(file));
      error.hidden = true;
      if (!file) return;
      if (!allowed.test(file.name)) {
        input.value = "";
        zone.classList.remove("has-file");
        error.textContent = zone.dataset.wrongType;
        error.hidden = false;
        return;
      }
      zone.querySelector("[data-file-name]").textContent = file.name;
      zone.querySelector("[data-file-size]").textContent = formatSize(file.size);
    };
    input.addEventListener("change", show);

    // Highlight while a file is dragged over. A counter avoids flicker when the
    // pointer moves over the elements inside the zone.
    let depth = 0;
    zone.addEventListener("dragenter", (event) => {
      event.preventDefault();
      depth += 1;
      zone.classList.add("is-dragging");
    });
    zone.addEventListener("dragover", (event) => event.preventDefault());
    zone.addEventListener("dragleave", () => {
      depth = Math.max(0, depth - 1);
      if (depth === 0) zone.classList.remove("is-dragging");
    });
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      depth = 0;
      zone.classList.remove("is-dragging");
      if (event.dataTransfer.files.length) {
        input.files = event.dataTransfer.files;
        show();
      }
    });
    show();
  }

  // A file dropped next to the drop area shouldn't make the browser open it instead.
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => event.preventDefault());

  initForms(document);

  /* ---------- "+ New" menu ---------- */

  const menu = $("#new-menu");
  const triggers = [...document.querySelectorAll("[data-menu-trigger]")];

  function setMenu(open) {
    if (!menu) return;
    menu.hidden = !open;
    triggers.forEach((t) => t.setAttribute("aria-expanded", String(open)));
    if (open) $("a", menu).focus();
  }

  triggers.forEach((trigger) =>
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      setMenu(menu.hidden);
    })
  );
  document.addEventListener("click", (event) => {
    if (menu && !menu.hidden && !menu.contains(event.target)) setMenu(false);
  });
  menu?.addEventListener("keydown", (event) => {
    // Arrow keys move between the menu items.
    const items = [...menu.querySelectorAll("a")];
    const index = items.indexOf(document.activeElement);
    if (event.key === "ArrowDown") { event.preventDefault(); items[(index + 1) % items.length].focus(); }
    if (event.key === "ArrowUp") { event.preventDefault(); items[(index - 1 + items.length) % items.length].focus(); }
  });

  /* ---------- Sliding panel ---------- */

  const panel = $("#panel");
  const panelBody = $("#panel-body");
  const backdrop = $("#panel-backdrop");
  let returnFocusTo = null;

  async function openPanel(url) {
    returnFocusTo = document.activeElement;
    let response;
    try {
      response = await fetch(url, { headers: { "X-Panel": "1" } });
    } catch {
      window.location.href = url;
      return;
    }
    if (!response.ok) { window.location.href = url; return; }

    showInPanel(await response.text(), url);
    panel.hidden = false;
    backdrop.hidden = false;
    document.body.classList.add("panel-open");
    panel.offsetWidth; // apply the start position before sliding in
    panel.classList.add("open");
    backdrop.classList.add("open");
  }

  function showInPanel(html, url) {
    panelBody.innerHTML = html;
    panel.dataset.url = url;
    initForms(panelBody);
    const first = panelBody.querySelector("[autofocus], input:not([type=hidden]):not([type=radio]), select, textarea");
    (first || panel).focus({ preventScroll: true });
  }

  function closePanel() {
    if (!panel || panel.hidden) return;
    panel.classList.remove("open");
    backdrop.classList.remove("open");
    document.body.classList.remove("panel-open");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setTimeout(() => {
      panel.hidden = true;
      backdrop.hidden = true;
      panelBody.innerHTML = "";
    }, reducedMotion ? 0 : 300);
    returnFocusTo?.focus?.();
  }

  // Open: any link with data-panel (menu items, "Add salary" on the dashboard...)
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-panel]");
    if (link && panel && !event.metaKey && !event.ctrlKey) {
      event.preventDefault();
      setMenu(false);
      openPanel(link.href);
      return;
    }
    if (panel && panel.contains(event.target) && event.target.closest("[data-panel-close]")) {
      event.preventDefault();
      closePanel();
    }
  });
  backdrop?.addEventListener("click", closePanel);
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (panel && !panel.hidden) closePanel();
    else if (menu && !menu.hidden) { setMenu(false); triggers[0].focus(); }
  });

  // Submit forms inside the panel without leaving the page.
  panelBody?.addEventListener("submit", async (event) => {
    const form = event.target;
    if (event.defaultPrevented) return; // e.g. "Cancel" in a "Delete this?" confirmation
    event.preventDefault();
    const button = form.querySelector('[type="submit"]');
    if (button) button.disabled = true;

    const url = form.getAttribute("action") || panel.dataset.url;
    let response;
    try {
      response = await fetch(url, { method: "POST", body: new FormData(form), headers: { "X-Panel": "1" } });
    } catch {
      form.submit(); // network problem: fall back to a normal submit
      return;
    }

    if ((response.headers.get("Content-Type") || "").includes("application/json")) {
      const { redirect } = await response.json();
      await goTo(redirect);
    } else {
      showInPanel(await response.text(), url); // the form again, with errors
    }
  });

  /* ---------- "Delete this?" confirmation dialog ---------- */
  //
  // Forms with data-confirm="Question?" ask first, in our own dialog (centred,
  // themed) instead of the browser's confirm() popup. data-confirm-detail adds a
  // second line, e.g. the item's name.

  const confirmDialog = $("#confirm-dialog");

  function askConfirmation(form) {
    return new Promise((resolve) => {
      if (!confirmDialog?.showModal) { resolve(window.confirm(form.dataset.confirm)); return; }
      $("#confirm-title", confirmDialog).textContent = form.dataset.confirm;
      const detail = $("#confirm-detail", confirmDialog);
      detail.textContent = form.dataset.confirmDetail || "";
      detail.hidden = !form.dataset.confirmDetail;
      confirmDialog.returnValue = "";
      confirmDialog.addEventListener("close", () => resolve(confirmDialog.returnValue === "confirm"), { once: true });
      confirmDialog.showModal();
    });
  }

  // Clicking the dimmed area around the dialog cancels.
  confirmDialog?.addEventListener("click", (event) => {
    if (event.target === confirmDialog) confirmDialog.close("cancel");
  });

  // Capture phase: runs before the panel's own submit handling.
  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!form.dataset.confirm) return;
    if (form.dataset.confirmed) { delete form.dataset.confirmed; return; } // already said yes
    event.preventDefault();
    event.stopPropagation();
    if (!(await askConfirmation(form))) return;

    // Lists can be filtered in the browser after the page loaded: come back to what is shown now.
    const next = form.querySelector('input[name="next"]');
    if (next && form.hasAttribute("data-next-current")) next.value = location.pathname + location.search;

    form.dataset.confirmed = "1";
    form.requestSubmit();
  }, true);

  /* ---------- Notifications (toasts) ---------- */

  const TOAST_TIME = { success: 5000, info: 5000, warning: 7000, error: 9000 };

  function initToasts(root) {
    root.querySelectorAll(".toast:not([data-ready])").forEach((toast, index) => {
      toast.dataset.ready = "1";
      const level = ["error", "warning", "success", "info"].find((l) => toast.classList.contains(l)) || "info";
      const duration = TOAST_TIME[level];
      toast.style.setProperty("--toast-duration", `${duration}ms`);
      toast.style.animationDelay = `${index * 80}ms`;

      // The timer pauses while the pointer is over the toast.
      let remaining = duration;
      let startedAt = Date.now();
      let timer = setTimeout(() => dismissToast(toast), remaining);
      toast.addEventListener("mouseenter", () => {
        clearTimeout(timer);
        remaining -= Date.now() - startedAt;
        toast.classList.add("is-paused");
      });
      toast.addEventListener("mouseleave", () => {
        startedAt = Date.now();
        timer = setTimeout(() => dismissToast(toast), Math.max(remaining, 1200));
        toast.classList.remove("is-paused");
      });
    });
  }

  function dismissToast(toast) {
    if (toast.classList.contains("is-leaving")) return;
    toast.classList.add("is-leaving");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    setTimeout(() => toast.remove(), reducedMotion ? 0 : 250);
  }

  document.addEventListener("click", (event) => {
    const close = event.target.closest?.("[data-toast-close]");
    if (close) dismissToast(close.closest(".toast"));
  });
  initToasts(document);

  /* ---------- Donut chart: hover / tap a category ---------- */
  //
  // Hovering a segment or legend row previews it; clicking keeps it selected
  // (clicking again, clicking elsewhere or pressing Esc clears it).

  const chartItem = (target) => target.closest?.("[data-chart] [data-index]");

  function highlight(chart, index) {
    const row = chart.querySelector(`.legend-item[data-index="${index}"]`);
    if (!row) return;
    chart.classList.add("has-focus");
    chart.querySelectorAll("[data-index]").forEach((el) => {
      el.classList.toggle("is-active", el.dataset.index === index);
    });
    $("[data-detail-label]", chart).textContent = row.dataset.label;
    $("[data-detail-amount]", chart).textContent = row.dataset.amount;
    $("[data-detail-text]", chart).textContent = row.dataset.detail;
    $("[data-detail-dot]", chart).style.background = row.dataset.color;
  }

  function clearHighlight(chart) {
    chart.classList.remove("has-focus");
    chart.querySelectorAll(".is-active").forEach((el) => el.classList.remove("is-active"));
  }

  function setLocked(chart, index) {
    chart.dataset.locked = index ?? "";
    chart.querySelectorAll(".legend-item").forEach((row) => {
      row.setAttribute("aria-pressed", String(row.dataset.index === index));
      if (row.hasAttribute("aria-expanded")) row.setAttribute("aria-expanded", String(row.dataset.index === index));
    });
    // Slide open the selected category's expenses (one at a time).
    chart.querySelectorAll("[data-details-for]").forEach((details) => {
      const open = details.dataset.detailsFor === index;
      details.classList.toggle("is-open", open);
      details.inert = !open;
    });
    if (index == null) clearHighlight(chart);
    else highlight(chart, index);
  }

  document.addEventListener("pointerover", (event) => {
    const item = chartItem(event.target);
    if (!item || event.pointerType === "touch") return;
    const chart = item.closest("[data-chart]");
    if (!chart.dataset.locked) highlight(chart, item.dataset.index);
  });
  document.addEventListener("pointerout", (event) => {
    const item = chartItem(event.target);
    if (!item || item.contains(event.relatedTarget)) return;
    const chart = item.closest("[data-chart]");
    if (chart.dataset.locked) highlight(chart, chart.dataset.locked);
    else if (!chartItem(event.relatedTarget || document.body)) clearHighlight(chart);
  });
  document.addEventListener("click", (event) => {
    // Clicks inside the open list, or on the Month | Trend switch, keep the selection.
    if (event.target.closest?.(".legend-details, [data-chart-views]")) return;
    const item = chartItem(event.target);
    document.querySelectorAll("[data-chart]").forEach((chart) => {
      if (item && chart.contains(item)) {
        const index = item.dataset.index;
        setLocked(chart, chart.dataset.locked === index ? null : index);
      } else if (chart.dataset.locked) {
        setLocked(chart, null); // clicked somewhere else
      }
    });
  });
  // Keyboard: Tab through the legend rows to preview each one.
  document.addEventListener("focusin", (event) => {
    const item = chartItem(event.target);
    if (item) highlight(item.closest("[data-chart]"), item.dataset.index);
  });
  document.addEventListener("focusout", (event) => {
    const item = chartItem(event.target);
    if (!item || chartItem(event.relatedTarget || document.body)) return;
    const chart = item.closest("[data-chart]");
    if (chart.dataset.locked) highlight(chart, chart.dataset.locked);
    else clearHighlight(chart);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    document.querySelectorAll("[data-chart]").forEach((chart) => setLocked(chart, null));
  });

  // "See all (12)" / "Show less" under a category's list.
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-show-all]");
    if (!button) return;
    const list = button.parentElement.querySelector(".legend-entries");
    const expanded = list.classList.toggle("is-collapsed") === false;
    button.setAttribute("aria-expanded", String(expanded));
    button.textContent = expanded ? button.dataset.less : button.dataset.more;
  });

  /* ---------- Transactions filter (All / Expenses / Income) ---------- */

  const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // The highlight "pill" behind the active option slides to its new place.
  function moveIndicator(nav, animate = true) {
    const active = nav.querySelector('a[aria-current="true"]');
    const indicator = nav.querySelector(".segmented-indicator");
    if (!active || !indicator) return;
    if (!animate) indicator.style.transition = "none";
    indicator.style.width = `${active.offsetWidth}px`;
    indicator.style.transform = `translateX(${active.offsetLeft}px)`;
    if (!animate) {
      indicator.offsetWidth; // apply the position before turning transitions back on
      indicator.style.transition = "";
    }
  }

  function initSegmented(root) {
    root.querySelectorAll(".segmented").forEach((nav) => {
      if (!nav.querySelector(".segmented-indicator")) {
        const indicator = document.createElement("span");
        indicator.className = "segmented-indicator";
        indicator.setAttribute("aria-hidden", "true");
        nav.prepend(indicator);
      }
      nav.classList.add("has-indicator");
      moveIndicator(nav, false);
    });
  }
  window.addEventListener("resize", () => document.querySelectorAll(".segmented").forEach((nav) => moveIndicator(nav, false)));
  document.fonts?.ready.then(() => document.querySelectorAll(".segmented").forEach((nav) => moveIndicator(nav, false)));

  // Items that leave fade out, then the new ones fade in one after another,
  // while the list's height changes smoothly.
  // Search text: lowercase and without accents, so "farmacia" finds "Farmácia".
  const fold = (text) => (text || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

  // Highlight the searched words in a transaction's name.
  function highlightEntry(entry, query) {
    const name = entry.querySelector(".entry-desc");
    if (!name) return;
    if (name.dataset.text === undefined) name.dataset.text = name.textContent;
    const text = name.dataset.text;
    const index = query ? fold(text).indexOf(fold(query.trim())) : -1;
    if (index < 0) { name.textContent = text; return; }
    const end = index + query.trim().length;
    name.replaceChildren(text.slice(0, index), el("mark", { text: text.slice(index, end) }), text.slice(end));
  }

  const currentFilter = (area) => area.querySelector('a[data-filter][aria-current="true"]')?.dataset.filter || "all";

  let filterRun = 0;
  async function applyFilter(area, type, { instant = false } = {}) {
    const run = ++filterRun;
    const list = area.querySelector(".entries");
    const entries = [...area.querySelectorAll(".entry[data-kind]")];
    const query = area.dataset.query || "";
    const words = fold(query).split(/\s+/).filter(Boolean);
    const matches = (entry) =>
      (type === "all" || entry.dataset.kind === type) &&
      words.every((word) => fold(entry.dataset.search).includes(word));
    const leaving = entries.filter((entry) => !entry.hidden && !matches(entry));
    const entering = entries.filter((entry) => entry.hidden && matches(entry));
    const animate = !reducedMotion() && !instant;
    const startHeight = list?.offsetHeight ?? 0;
    entries.forEach((entry) => highlightEntry(entry, query));

    if (animate && leaving.length) {
      leaving.forEach((entry) => entry.classList.add("is-leaving"));
      await wait(140);
      if (run !== filterRun) return; // another filter was clicked meanwhile
    }
    leaving.forEach((entry) => { entry.hidden = true; entry.classList.remove("is-leaving"); });
    entering.forEach((entry, index) => {
      entry.hidden = false;
      if (!animate) return;
      entry.classList.remove("is-entering");
      entry.offsetWidth; // restart the animation
      entry.style.animationDelay = `${Math.min(index, 8) * 25}ms`;
      entry.classList.add("is-entering");
    });

    const visible = entries.filter((entry) => !entry.hidden).length;
    area.querySelectorAll("[data-empty-for]").forEach((message) => {
      message.hidden = !(message.dataset.emptyFor === type && visible === 0 && !query);
    });
    const noResults = area.querySelector("[data-empty-search]");
    if (noResults) {
      noResults.hidden = !(query && visible === 0);
      noResults.textContent = (noResults.dataset.template || "").replace("%s", query.trim());
    }

    if (animate && list) {
      const endHeight = list.offsetHeight;
      if (endHeight !== startHeight) {
        list.style.overflow = "hidden";
        list.animate([{ height: `${startHeight}px` }, { height: `${endHeight}px` }],
                     { duration: 260, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" })
          .finished.finally(() => { list.style.overflow = ""; });
      }
    }
  }

  // Typing in the search box filters instantly (together with All / Expenses / Income).
  document.addEventListener("input", (event) => {
    const input = event.target.closest?.("[data-transactions-search]");
    if (!input) return;
    const area = input.closest("[data-filter-area]");
    area.dataset.query = input.value;
    applyFilter(area, currentFilter(area), { instant: true });
  });
  document.addEventListener("keydown", (event) => {
    const input = event.target.closest?.("[data-transactions-search]");
    if (input && event.key === "Escape" && input.value) {
      event.stopPropagation();
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }
  }, true);

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-filter]");
    if (!link || event.metaKey || event.ctrlKey) return;
    const area = link.closest("[data-filter-area]");
    if (!area) return;
    event.preventDefault();
    if (link.getAttribute("aria-current") === "true") return;

    area.querySelectorAll("a[data-filter]").forEach((a) => {
      if (a === link) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
    moveIndicator(link.closest(".segmented"));
    applyFilter(area, link.dataset.filter);
    history.replaceState({}, "", link.href); // keep the filter if the page is reloaded or shared
  });

  /* ---------- Chart card: Month | Trend, and the line chart ---------- */

  function initChartCards(root) {
    root.querySelectorAll("[data-chart-card]").forEach((card) => {
      const views = card.querySelector("[data-chart-views]");
      if (views) views.hidden = false; // the switch needs JavaScript, so it starts hidden
      card.dataset.range = card.dataset.range || "6";
    });
  }

  function trendData(card) {
    try { return JSON.parse(card.querySelector("#trend-data").textContent); } catch { return null; }
  }

  function formatAmount(value, currency, decimals = 2) {
    const [whole, cents] = Math.abs(value).toFixed(decimals).split(".");
    const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, currency.thousands);
    const space = currency.space ? " " : "";
    return `${value < 0 ? "-" : ""}${currency.symbol}${space}${grouped}${cents ? currency.decimal + cents : ""}`;
  }

  // A "round" step for the axis: 1, 2, 2.5 or 5 times a power of ten.
  function niceStep(rough) {
    if (rough <= 0) return 1;
    const power = 10 ** Math.floor(Math.log10(rough));
    const fraction = rough / power;
    return (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 2.5 ? 2.5 : fraction <= 5 ? 5 : 10) * power;
  }

  const SVG_NS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  }

  let trendId = 0;
  function renderTrend(card) {
    const box = card.querySelector("[data-trend-chart]");
    const data = trendData(card);
    if (!box || !data || !box.clientWidth) return;
    const range = Number(card.dataset.range || 6);
    const months = data.months.slice(-range);
    const currency = data.currency;

    // Which lines: one category (if chosen in the donut), or expenses + income.
    const categoryId = card.dataset.trendCategory || "";
    const series = categoryId
      ? [{ values: months.map((m) => m.categories[categoryId] || 0), color: card.dataset.trendColor, label: card.dataset.trendLabel, area: true }]
      : [
          { values: months.map((m) => m.expenses), color: "var(--text)", label: box.dataset.labelExpenses, area: true },
          { values: months.map((m) => m.income), color: "var(--positive)", label: box.dataset.labelIncome, dashed: true },
        ];

    // Title, "× category" chip and legend
    const title = card.querySelector("[data-trend-title]");
    const clear = card.querySelector("[data-trend-clear]");
    title.textContent = categoryId ? "" : title.dataset.all;
    clear.hidden = !categoryId;
    if (categoryId) {
      card.querySelector("[data-trend-category]").textContent = card.dataset.trendLabel;
      card.querySelector("[data-trend-dot]").style.background = card.dataset.trendColor;
    }
    card.querySelector("[data-trend-legend]").hidden = Boolean(categoryId);

    box.replaceChildren();
    const max = Math.max(0, ...series.flatMap((s) => s.values));
    if (max === 0) {
      box.append(el("p", { class: "trend-empty muted", text: box.dataset.empty }));
      return;
    }

    // Geometry
    const width = box.clientWidth;
    const height = 220;
    const pad = { top: 14, right: 14, bottom: 30, left: 70 };
    const step = niceStep(max / 3);
    const top = Math.ceil(max / step) * step;
    const x = (i) => pad.left + (months.length === 1 ? 0 : (i * (width - pad.left - pad.right)) / (months.length - 1));
    const y = (v) => pad.top + (1 - v / top) * (height - pad.top - pad.bottom);
    const id = `trend-${++trendId}`;

    const chart = svg("svg", { width, height, viewBox: `0 0 ${width} ${height}`, class: "trend-svg", role: "img" });
    const defs = svg("defs");
    const clip = svg("clipPath", { id: `${id}-clip` });
    clip.append(svg("rect", { class: "trend-reveal", x: 0, y: 0, width, height }));
    defs.append(clip);
    chart.append(defs);

    // Grid lines and amounts on the left
    for (let value = 0; value <= top + 0.001; value += step) {
      chart.append(svg("line", { class: "trend-grid", x1: pad.left, x2: width - pad.right, y1: y(value), y2: y(value) }));
      const label = svg("text", { class: "trend-axis", x: pad.left - 10, y: y(value) + 4, "text-anchor": "end" });
      label.textContent = formatAmount(value, currency, 0);
      chart.append(label);
    }

    // Month names along the bottom (every other one on narrow screens with 12 months)
    const fmtMonth = new Intl.DateTimeFormat(LANG, { month: "short" });
    const fmtMonthLong = new Intl.DateTimeFormat(LANG, { month: "long", year: "numeric" });
    const dates = months.map((m) => { const [yy, mm] = m.month.split("-").map(Number); return new Date(yy, mm - 1, 1); });
    const skip = months.length > 6 && width < 520 ? 2 : 1;
    dates.forEach((date, i) => {
      if ((months.length - 1 - i) % skip) return;
      const label = svg("text", { class: `trend-axis${i === months.length - 1 ? " is-current" : ""}`, x: x(i), y: height - 8, "text-anchor": "middle" });
      label.textContent = capitalize(fmtMonth.format(date).replace(".", ""));
      chart.append(label);
    });

    // Lines (with a soft area under the main one), revealed left to right
    const lines = svg("g", { "clip-path": `url(#${id}-clip)` });
    series.forEach((s, index) => {
      const points = s.values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
      if (s.area) {
        const gradient = svg("linearGradient", { id: `${id}-fill-${index}`, x1: 0, y1: 0, x2: 0, y2: 1 });
        gradient.append(svg("stop", { offset: "0%", "stop-color": s.color, "stop-opacity": 0.16 }));
        gradient.append(svg("stop", { offset: "100%", "stop-color": s.color, "stop-opacity": 0 }));
        defs.append(gradient);
        const baseline = y(0).toFixed(1);
        lines.append(svg("path", {
          d: `M${x(0).toFixed(1)},${baseline} L${points.join(" L")} L${x(months.length - 1).toFixed(1)},${baseline} Z`,
          fill: `url(#${id}-fill-${index})`,
        }));
      }
      lines.append(svg("path", {
        class: "trend-line", d: `M${points.join(" L")}`, stroke: s.color,
        ...(s.dashed ? { "stroke-dasharray": "5 5" } : {}),
      }));
      s.values.forEach((v, i) => {
        lines.append(svg("circle", {
          class: `trend-point${i === months.length - 1 ? " is-current" : ""}`,
          cx: x(i), cy: y(v), r: i === months.length - 1 ? 4.5 : 3.2, stroke: s.color,
          ...(i === months.length - 1 ? { fill: s.color } : {}),
        }));
      });
    });
    chart.append(lines);

    // Hover / focus: a guide line and a tooltip. Clicking a month opens it.
    const guide = svg("line", { class: "trend-guide", y1: pad.top, y2: height - pad.bottom, x1: 0, x2: 0, visibility: "hidden" });
    chart.append(guide);
    const tooltip = el("div", { class: "trend-tooltip", hidden: true });
    const column = months.length === 1 ? width : (width - pad.left - pad.right) / (months.length - 1);
    months.forEach((month, i) => {
      const summary = series.map((s) => `${s.label}: ${formatAmount(s.values[i], currency)}`).join(", ");
      const hit = svg("rect", {
        class: "trend-hit", x: x(i) - column / 2, y: 0, width: column, height,
        tabindex: 0, role: "link", "aria-label": `${capitalize(fmtMonthLong.format(dates[i]))}. ${summary}`,
      });
      const show = () => {
        guide.setAttribute("x1", x(i)); guide.setAttribute("x2", x(i)); guide.setAttribute("visibility", "visible");
        tooltip.replaceChildren(
          el("strong", { text: capitalize(fmtMonthLong.format(dates[i])) }),
          ...series.map((s) => el("span", { class: "trend-tooltip-row" }, [
            el("span", { class: `trend-key${s.dashed ? " trend-key-income" : ""}`, style: s.dashed ? "" : `background:${s.color}` }),
            el("span", { text: s.label }),
            el("b", { text: formatAmount(s.values[i], currency) }),
          ])),
        );
        tooltip.hidden = false;
        const left = Math.min(Math.max(x(i) - tooltip.offsetWidth / 2, 0), width - tooltip.offsetWidth);
        tooltip.style.left = `${left}px`;
      };
      const hide = () => { guide.setAttribute("visibility", "hidden"); tooltip.hidden = true; };
      const open = () => { window.location.href = `?month=${month.month}`; };
      hit.addEventListener("pointerenter", show);
      hit.addEventListener("pointerleave", hide);
      hit.addEventListener("focus", show);
      hit.addEventListener("blur", hide);
      hit.addEventListener("click", open);
      hit.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } });
      chart.append(hit);
    });

    box.append(chart, tooltip);
  }

  function showChartView(card, view) {
    const panels = [...card.querySelectorAll("[data-view-panel]")];
    const current = panels.find((p) => !p.hidden);
    const next = panels.find((p) => p.dataset.viewPanel === view);
    if (!next || next === current) return;

    if (view === "trend") {
      // A category selected in the donut? Show its trend.
      const locked = card.querySelector("[data-chart]")?.dataset.locked;
      const row = locked && locked !== "left" ? card.querySelector(`.legend-item[data-index="${locked}"]`) : null;
      card.dataset.trendCategory = row?.dataset.categoryId || "";
      card.dataset.trendLabel = row?.dataset.label || "";
      card.dataset.trendColor = row?.dataset.color || "";
    }

    current.hidden = true;
    next.hidden = false;
    if (!reducedMotion()) {
      next.classList.remove("view-from-right", "view-from-left");
      next.offsetWidth;
      next.classList.add(view === "trend" ? "view-from-right" : "view-from-left");
    }
    if (view === "trend") renderTrend(card);
  }

  document.addEventListener("click", (event) => {
    const viewLink = event.target.closest?.("a[data-view]");
    const rangeLink = event.target.closest?.("a[data-range]");
    const clearButton = event.target.closest?.("[data-trend-clear]");
    const card = (viewLink || rangeLink || clearButton)?.closest("[data-chart-card]");
    if (!card) return;
    event.preventDefault();

    if (viewLink || rangeLink) {
      const link = viewLink || rangeLink;
      const nav = link.closest(".segmented");
      nav.querySelectorAll("a").forEach((a) => {
        if (a === link) a.setAttribute("aria-current", "true");
        else a.removeAttribute("aria-current");
      });
      moveIndicator(nav);
    }
    if (viewLink) showChartView(card, viewLink.dataset.view);
    if (rangeLink) { card.dataset.range = rangeLink.dataset.range; renderTrend(card); }
    if (clearButton) { card.dataset.trendCategory = ""; renderTrend(card); }
  });

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      document.querySelectorAll("[data-chart-card]").forEach((card) => {
        if (!card.querySelector('[data-view-panel="trend"]')?.hidden) renderTrend(card);
      });
    }, 150);
  });

  /* ---------- AI assistant (ask bar on the dashboard) ---------- */
  //
  // Slim bar -> grows into a small chat when you ask -> can expand over the page.
  // Everything uses event delegation, because the dashboard's content can be
  // swapped in place (after adding an expense, for example).

  // Replies may use **bold** and "- " lists. Build them as DOM nodes (never as HTML),
  // so nothing in a reply can inject markup.
  function formatReply(text) {
    const fragment = document.createDocumentFragment();
    const inline = (line) => {
      const nodes = [];
      line.split(/(\*\*[^*]+\*\*)/g).forEach((part) => {
        if (!part) return;
        nodes.push(part.startsWith("**") && part.endsWith("**")
          ? el("strong", { text: part.slice(2, -2) })
          : document.createTextNode(part));
      });
      return nodes;
    };
    text.split(/\n{2,}/).forEach((block) => {
      const lines = block.split("\n").filter((line) => line.trim());
      if (!lines.length) return;
      if (lines.every((line) => /^\s*[-•*]\s+/.test(line))) {
        const list = el("ul");
        lines.forEach((line) => list.append(el("li", {}, inline(line.replace(/^\s*[-•*]\s+/, "")))));
        fragment.append(list);
      } else {
        const paragraph = el("p");
        lines.forEach((line, index) => {
          if (index) paragraph.append(el("br"));
          paragraph.append(...inline(line));
        });
        fragment.append(paragraph);
      }
    });
    return fragment;
  }

  function addMessage(box, role, text, extra = "") {
    const list = box.querySelector("[data-assistant-messages]");
    const message = el("div", { class: `assistant-msg ${role} ${extra}`.trim() });
    if (role === "assistant") message.append(formatReply(text));
    else message.textContent = text;
    list.append(message);
    list.scrollTop = list.scrollHeight;
    return message;
  }

  async function openAssistant(box) {
    if (box.classList.contains("is-open")) return;
    box.classList.add("is-open");
    if (box.dataset.loaded) return;
    box.dataset.loaded = "1";
    try {
      const response = await fetch(box.dataset.historyUrl);
      const data = await response.json();
      const list = box.querySelector("[data-assistant-messages]");
      if (!list.children.length) {
        data.messages.forEach((m) => {
          addMessage(box, m.role, m.text);
          (m.drafts || []).forEach((draft) => addDraftCard(box, draft));
        });
      }
    } catch { /* no history: start empty */ }
  }

  function setExpanded(box, expanded) {
    const backdrop = $("[data-assistant-backdrop]");
    if (expanded === box.classList.contains("is-expanded")) return;
    if (expanded) {
      // Keep the page from jumping while the assistant floats above it.
      const placeholder = el("div", { class: "assistant-placeholder" });
      placeholder.style.height = `${box.offsetHeight}px`;
      box.after(placeholder);
      box.classList.add("is-expanded");
      if (backdrop) backdrop.hidden = false;
      document.body.classList.add("panel-open");
      box.querySelector('input[name="message"]')?.focus({ preventScroll: true }); // ready to type
    } else {
      box.classList.remove("is-expanded");
      box.parentNode.querySelector(".assistant-placeholder")?.remove();
      if (backdrop) backdrop.hidden = true;
      document.body.classList.remove("panel-open");
    }
    box.querySelector("[data-assistant-expand]")?.setAttribute("aria-pressed", String(expanded));
    const list = box.querySelector("[data-assistant-messages]");
    list.scrollTop = list.scrollHeight;
  }

  function closeAssistant(box) {
    setExpanded(box, false);
    box.classList.remove("is-open");
  }

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest?.("[data-assistant-form]");
    if (!form) return;
    event.preventDefault();
    const box = form.closest("[data-assistant]");
    const input = form.querySelector('input[name="message"]');
    const question = input.value.trim();
    const send = form.querySelector(".assistant-send");
    if (!question || send.disabled) return;

    const body = new FormData(form);
    input.value = "";
    await openAssistant(box);
    addMessage(box, "user", question);
    const thinking = addMessage(box, "assistant", "", "is-thinking");
    thinking.replaceChildren(el("span", { class: "thinking-dots", "aria-label": box.dataset.thinking }, [el("span"), el("span"), el("span")]));
    send.disabled = true;

    try {
      const response = await fetch(form.action, { method: "POST", body, headers: { "X-Requested-With": "fetch" } });
      const data = await response.json();
      thinking.remove();
      if (data.error) addMessage(box, "assistant", data.error, "is-error");
      else {
        addMessage(box, "assistant", data.reply, data.configured === false ? "is-muted" : "");
        (data.drafts || []).forEach((draft) => addDraftCard(box, draft));
      }
    } catch {
      thinking.remove();
      addMessage(box, "assistant", box.dataset.error, "is-error");
    } finally {
      send.disabled = false;
      input.focus();
    }
  });

  // Suggestion chips: keep the focus in the bar while clicking (so they don't disappear first).
  document.addEventListener("pointerdown", (event) => {
    if (event.target.closest?.("[data-suggestion]")) event.preventDefault();
  });

  document.addEventListener("click", async (event) => {
    const box = assistantFor(event.target);
    if (!box) {
      if (event.target.closest?.("[data-assistant-backdrop]")) {
        document.querySelectorAll("[data-assistant].is-expanded").forEach((b) => setExpanded(b, false));
      }
      return;
    }
    const suggestion = event.target.closest("[data-suggestion]");
    if (suggestion) {
      const form = box.querySelector("[data-assistant-form]");
      form.querySelector('input[name="message"]').value = suggestion.textContent.trim();
      form.requestSubmit();
      return;
    }
    const draftButton = event.target.closest("[data-draft-action]");
    if (draftButton) { resolveDraft(box, draftButton.closest(".draft-card"), draftButton.dataset.draftAction); return; }
    const foundDelete = event.target.closest("[data-found-delete]");
    if (foundDelete) { deleteFoundRow(box, foundDelete.closest(".found-row")); return; }
    if (event.target.closest("[data-assistant-close]")) { closeAssistant(box); return; }
    if (event.target.closest("[data-assistant-expand]")) { setExpanded(box, !box.classList.contains("is-expanded")); return; }
    if (event.target.closest("[data-assistant-reset]")) {
      const token = box.querySelector('input[name="csrfmiddlewaretoken"]')?.value || "";
      box.querySelector("[data-assistant-messages]").replaceChildren();
      try {
        await fetch(box.dataset.resetUrl, { method: "POST", headers: { "X-CSRFToken": token } });
      } catch { /* the next question starts a new conversation anyway */ }
      box.querySelector('input[name="message"]').focus();
    }
  });

  function assistantFor(target) {
    return target.closest?.("[data-assistant]") || null;
  }

  const metaText = (item) =>
    [item.category_label, item.date_display, item.repeats !== "once" ? item.repeats_label : null].filter(Boolean).join(" · ");

  // Transactions the assistant found: rows with Open (details page) and Delete.
  function addFoundCard(box, card) {
    const list = box.querySelector("[data-assistant-messages]");
    const labels = box.dataset;
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    list.append(el("div", { class: "found-card" }, card.items.map((item) => el("div", {
      class: "found-row", "data-delete-url": item.delete_url, "data-description": item.description,
    }, [
      el("a", { class: "found-main", href: `${item.url}?next=${next}`, title: labels.labelOpen }, [
        el("span", { class: "found-name", text: item.description }),
        el("span", { class: "found-meta" }, [
          el("span", { class: "chip-dot", style: `background:${item.category_color}` }),
          el("span", { text: metaText(item) }),
        ]),
      ]),
      el("span", { class: `found-amount${item.kind === "income" ? " positive" : ""}`, text: (item.kind === "income" ? "+" : "") + item.amount_display }),
      el("button", { type: "button", class: "icon-btn small", "data-found-delete": "", "aria-label": `${labels.labelDelete}: ${item.description}`, text: "×" }),
    ]))));
    list.scrollTop = list.scrollHeight;
  }

  // A proposed edit (before -> after) or delete of an existing transaction.
  function addChangeCard(box, draft) {
    const list = box.querySelector("[data-assistant-messages]");
    const labels = box.dataset;
    const parts = [
      el("div", { class: "draft-top" }, [
        el("span", { class: "draft-kind", text: draft.title }),
        el("span", { class: "draft-amount", text: draft.amount_display }),
      ]),
      el("div", { class: "draft-desc", text: draft.description }),
      el("div", { class: "draft-meta" }, [
        el("span", { class: "chip-dot", style: `background:${draft.category_color}` }),
        el("span", { text: metaText(draft) }),
      ]),
    ];
    if (draft.changes.length) {
      parts.push(el("div", { class: "change-rows" }, draft.changes.map((change) => el("div", { class: "change-row" }, [
        el("span", { class: "change-label", text: change.label }),
        el("span", { class: "change-before", text: change.before }),
        el("span", { class: "change-arrow", "aria-hidden": "true", text: "→" }),
        el("span", { class: "change-after", text: change.after }),
      ]))));
    }
    if (draft.recurring) parts.push(el("p", { class: "change-note", text: labels.labelEveryMonth }));
    parts.push(
      el("div", { class: "draft-actions" }, [
        el("button", { type: "button", class: "btn small", "data-draft-action": "cancel", text: labels.labelCancel }),
        el("button", { type: "button", class: `btn small ${draft.action === "delete" ? "danger" : "primary"}`, "data-draft-action": "confirm", text: draft.button_label }),
      ]),
      el("div", { class: "draft-status", text: draft.status === "confirmed" ? `✓ ${draft.done_label}` : labels.labelCancelled }),
    );
    list.append(el("div", {
      class: `draft-card change-card is-${draft.action} is-${draft.status}`, "data-draft-id": draft.id, "data-done-label": draft.done_label,
    }, parts));
    list.scrollTop = list.scrollHeight;
  }

  async function deleteFoundRow(box, row) {
    const ok = await askConfirmation({ dataset: { confirm: box.dataset.labelDeleteQuestion, confirmDetail: row.dataset.description } });
    if (!ok) return;
    const token = box.querySelector('input[name="csrfmiddlewaretoken"]')?.value || "";
    try {
      const response = await fetch(row.dataset.deleteUrl, { method: "POST", headers: { "X-CSRFToken": token, "X-Requested-With": "fetch" } });
      if (!response.ok) throw new Error(String(response.status));
      const data = await response.json();
      row.classList.add("is-deleted");
      row.querySelector("[data-found-delete]")?.remove();
      showToast(data.message, box.dataset.labelClose);
      refreshDashboard();
    } catch {
      addMessage(box, "assistant", box.dataset.error, "is-error");
    }
  }

  // A card for an expense/income the assistant prepared. Saved only when the user clicks Add.
  function addDraftCard(box, draft) {
    if (draft.type === "found") { addFoundCard(box, draft); return; }
    if (draft.type === "change") { addChangeCard(box, draft); return; }
    const list = box.querySelector("[data-assistant-messages]");
    const labels = box.dataset;
    const meta = [draft.date_display, draft.repeats === "once" ? null : (
      draft.installments ? `${draft.repeats_label} · ${draft.installments}×` : draft.repeats_label
    )].filter(Boolean).join(" · ");
    const card = el("div", { class: `draft-card is-${draft.status}`, "data-draft-id": draft.id }, [
      el("div", { class: "draft-top" }, [
        el("span", { class: `draft-kind is-${draft.kind}`, text: draft.kind_label }),
        el("span", { class: "draft-amount", text: (draft.kind === "income" ? "+" : "") + draft.amount_display }),
      ]),
      el("div", { class: "draft-desc", text: draft.description }),
      el("div", { class: "draft-meta" }, [
        el("span", { class: "chip-dot", style: `background:${draft.category_color}` }),
        el("span", { text: draft.category_label }),
        el("span", { text: meta ? ` · ${meta}` : "" }),
      ]),
      el("div", { class: "draft-actions" }, [
        el("button", { type: "button", class: "btn small", "data-draft-action": "cancel", text: labels.labelCancel }),
        el("button", { type: "button", class: "btn small primary", "data-draft-action": "confirm", text: labels.labelAdd }),
      ]),
      el("div", { class: "draft-status", text: draft.status === "confirmed" ? `✓ ${labels.labelAdded}` : labels.labelCancelled }),
    ]);
    list.append(card);
    list.scrollTop = list.scrollHeight;
  }

  async function resolveDraft(box, card, action) {
    const token = box.querySelector('input[name="csrfmiddlewaretoken"]')?.value || "";
    const url = box.dataset.draftUrl.replace("DRAFT", card.dataset.draftId).replace("ACTION", action);
    card.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    try {
      const response = await fetch(url, { method: "POST", headers: { "X-CSRFToken": token } });
      const data = await response.json();
      if (data.error) {
        addMessage(box, "assistant", data.error, "is-error");
        card.querySelectorAll("button").forEach((b) => { b.disabled = false; });
        return;
      }
      card.classList.remove("is-pending");
      card.classList.add(`is-${data.status}`);
      const doneLabel = card.dataset.doneLabel || box.dataset.labelAdded;
      card.querySelector(".draft-status").textContent = data.status === "confirmed" ? `✓ ${doneLabel}` : box.dataset.labelCancelled;
      if (data.message) {
        showToast(data.message, box.dataset.labelClose);
        refreshDashboard();
      }
    } catch {
      addMessage(box, "assistant", box.dataset.error, "is-error");
      card.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    }
  }

  function showToast(text, closeLabel) {
    const toasts = $(".toasts");
    if (!toasts) return;
    toasts.append(el("div", { class: "toast success", role: "status" }, [
      el("span", { class: "toast-icon", "aria-hidden": "true", text: "✓" }),
      el("span", { class: "toast-text", text }),
      el("button", { type: "button", class: "toast-close", "data-toast-close": "", "aria-label": closeLabel, text: "×" }),
      el("span", { class: "toast-timer", "aria-hidden": "true" }),
    ]));
    initToasts(toasts);
  }

  // After saving from the chat: refresh the chart and the transactions, but keep the chat as it is.
  async function refreshDashboard() {
    try {
      const response = await fetch(window.location.href);
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      ["[data-chart-card]", "[data-filter-area]"].forEach((selector) => {
        const current = $(selector);
        const fresh = $(selector, page);
        if (current && fresh) current.replaceWith(fresh);
      });
      initForms($("main.container"));
    } catch { /* the next page load will show it */ }
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const expanded = document.querySelector("[data-assistant].is-expanded");
    if (expanded) { setExpanded(expanded, false); return; }
    const open = document.activeElement?.closest?.("[data-assistant].is-open");
    if (open) closeAssistant(open);
  });

  /* ---------- Tabs (Settings: General / Categories / Account) ---------- */

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-tab]");
    if (!link || event.metaKey || event.ctrlKey) return;
    const area = link.closest("[data-tabs]");
    if (!area) return;
    event.preventDefault();
    if (link.getAttribute("aria-current") === "true") return;

    area.querySelectorAll("a[data-tab]").forEach((a) => {
      if (a === link) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
    moveIndicator(link.closest(".segmented"));
    area.querySelectorAll("[data-tab-panel]").forEach((panel) => {
      const show = panel.dataset.tabPanel === link.dataset.tab;
      panel.hidden = !show;
      if (show && !reducedMotion()) {
        panel.classList.remove("tab-enter");
        panel.offsetWidth; // restart the animation
        panel.classList.add("tab-enter");
      }
    });
    history.replaceState({}, "", link.href);
  });

  /* ---------- Update the page in place ---------- */

  // After saving in the panel: if we stay on the same page (dashboard, settings...),
  // swap in its fresh content instead of reloading. Otherwise, go to the new page.
  async function goTo(url) {
    const target = new URL(url, window.location.href);
    if (target.pathname !== window.location.pathname) {
      window.location.href = target; // e.g. the import review page
      return;
    }
    try {
      const response = await fetch(target);
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const main = $("main.container");
      main.innerHTML = $("main.container", page).innerHTML;
      initForms(main);
      // Bring over the new notifications (e.g. "Added …").
      const toasts = $(".toasts");
      $(".toasts", page)?.querySelectorAll(".toast").forEach((toast) => toasts.append(toast));
      initToasts(toasts);
      document.title = page.title;
      if (target.href !== window.location.href) history.pushState({}, "", target);
      closePanel();
    } catch {
      window.location.href = target;
    }
  }

  // We changed the URL with pushState, so Back/Forward needs a real reload.
  window.addEventListener("popstate", () => window.location.reload());
})();
