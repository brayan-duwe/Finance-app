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
    root.querySelectorAll("[data-dropzone]").forEach(initDropzone);
    root.querySelectorAll('select[name="frequency"]').forEach((select) => {
      const field = select.form && select.form.querySelector(".field-installment_total");
      if (!field) return;
      const sync = () => { field.hidden = select.value !== "installments"; };
      select.addEventListener("change", sync);
      sync();
    });
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

  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-filter]");
    if (!link || event.metaKey || event.ctrlKey) return;
    const area = link.closest("[data-filter-area]");
    if (!area) return;
    event.preventDefault();

    const type = link.dataset.filter;
    area.querySelectorAll("a[data-filter]").forEach((a) => {
      if (a === link) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
    let visible = 0;
    area.querySelectorAll(".entry[data-kind]").forEach((entry) => {
      entry.hidden = type !== "all" && entry.dataset.kind !== type;
      if (!entry.hidden) visible += 1;
    });
    area.querySelectorAll("[data-empty-for]").forEach((message) => {
      message.hidden = !(message.dataset.emptyFor === type && visible === 0);
    });
    history.replaceState({}, "", link.href); // keep the filter if the page is reloaded or shared
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
