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
    if (event.target.closest?.(".legend-details")) return; // clicks inside the open list keep it open
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
  let filterRun = 0;
  async function applyFilter(area, type) {
    const run = ++filterRun;
    const list = area.querySelector(".entries");
    const entries = [...area.querySelectorAll(".entry[data-kind]")];
    const matches = (entry) => type === "all" || entry.dataset.kind === type;
    const leaving = entries.filter((entry) => !entry.hidden && !matches(entry));
    const entering = entries.filter((entry) => entry.hidden && matches(entry));
    const animate = !reducedMotion();
    const startHeight = list?.offsetHeight ?? 0;

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
      message.hidden = !(message.dataset.emptyFor === type && visible === 0);
    });

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
