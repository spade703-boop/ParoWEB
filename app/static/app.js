/* ==========================================================================
   抽派生 · 前端交互
   视觉稿：Ardot file/727928496100182
   - 固定派生选择器：原生 <select> 兜底无 JS 场景，自定义浮层面板负责视觉，
     两者状态双向同步；面板是浮层，直接盖住下方「抽取数量」和「抽取」按钮。
   ========================================================================== */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const div = (className) => {
  const node = document.createElement("div");
  if (className) node.className = className;
  return node;
};
const span = (className, text) => {
  const node = document.createElement("span");
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
};
const textNode = (value) => document.createTextNode(value);

const SVG = {
  caretDown:
    '<svg viewBox="0 0 13 13" fill="none"><path d="M3.4 5.2L6.5 8.3L9.6 5.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  caretUp:
    '<svg viewBox="0 0 13 13" fill="none"><path d="M3.4 8.1L6.5 5L9.6 8.1" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  cross:
    '<svg viewBox="0 0 12 12" fill="none"><path d="M1.6 1.6L10.4 10.4M10.4 1.6L1.6 10.4" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  check:
    '<svg viewBox="0 0 13 13" fill="none"><path d="M2.2 6.9L5.1 9.8L10.8 3.4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  star:
    '<svg viewBox="0 0 12 12" fill="currentColor"><path d="M6 0.6L7.5 4.2L11.4 4.6L8.5 7.1L9.3 10.9L6 8.9L2.7 10.9L3.5 7.1L0.6 4.6L4.5 4.2Z"/></svg>',
  chevron:
    '<svg viewBox="0 0 12 12" fill="none"><path d="M4.2 2.4L7.8 6L4.2 9.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  chevronLeft:
    '<svg viewBox="0 0 12 12" fill="none"><path d="M7.8 2.4L4.2 6L7.8 9.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  spark30:
    '<svg width="30" height="30" viewBox="0 0 15 15" fill="none" aria-hidden="true"><path d="M7.5 0.8C7.9 4.3 10.7 7.1 14.2 7.5C10.7 7.9 7.9 10.7 7.5 14.2C7.1 10.7 4.3 7.9 0.8 7.5C4.3 7.1 7.1 4.3 7.5 0.8Z" fill="currentColor"/></svg>',
  search:
    '<svg viewBox="0 0 17 17" fill="none"><circle cx="7.3" cy="7.3" r="5" stroke="currentColor" stroke-width="1.5"/><path d="M11 11L15 15" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M5.3 7.3H9.3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
};

const MODE_LABEL = { none: "双方随机", akito: "固定彰人", toya: "固定冬弥" };
const SIDE_LABEL = { akito: "彰人", toya: "冬弥" };
const POOL_PAGE_SIZE = 9;
const POOL_SECTION_LIMIT = 8;

const state = {
  ready: false,
  catalog: null,
  profile: null,
  profileStale: true,
  fixedSide: "none",
  fixedName: null,
  activeIndex: 0,
  lastMode: "none",
  lastBatchAt: null,
  poolFilter: "all",
  poolSearch: "",
  poolPage: 1,
  poolExpanded: { akito: false, toya: false },
  recordFilter: "all",
};

const el = {
  views: $$("[data-view]"),
  navItems: $$(".top-nav__item, .bottom-nav__item"),
  headerCount: $("#header-count"),
  form: $("#draw-form"),
  drawButton: $("#draw-button"),
  drawStatus: $("#draw-status"),
  results: $("#results"),
  resultTime: $("#result-time"),
  metricDraws: $("#metric-draws"),
  metricCooking: $("#metric-cooking"),
  metricSpecial: $("#metric-special"),
  fixedLabel: $("#fixed-label"),
  picker: $("#picker"),
  pickerTrigger: $("#picker-trigger"),
  pickerAvatar: $("#picker-avatar"),
  pickerName: $("#picker-name"),
  pickerCaret: $("#picker-caret"),
  pickerPanel: $("#picker-panel"),
  pickerSearch: $("#picker-search"),
  pickerClear: $("#picker-clear"),
  pickerList: $("#picker-list"),
  pickerHint: $("#picker-hint"),
  fixedSelect: $("#fixed-name"),
  poolSummary: $("#pool-summary"),
  poolSearch: $("#pool-search"),
  poolFilters: $("#pool-filters"),
  poolSections: $("#pool-sections"),
  poolNote: $("#pool-note"),
  poolPager: $("#pool-pager"),
  statDraws: $("#stat-draws"),
  statCooking: $("#stat-cooking"),
  statSpecial: $("#stat-special"),
  statNormal: $("#stat-normal"),
  recordFilters: $("#record-filters"),
  recordsList: $("#records-list"),
  clearButton: $("#clear-button"),
  clearDialog: $("#clear-dialog"),
  confirmClear: $("#confirm-clear"),
};

const mqMobilePool = window.matchMedia("(max-width: 899px)");

/* ---------- 基础工具 ---------- */

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload?.error?.message || "请求失败，请稍后重试");
    error.retryAfter = response.headers.get("Retry-After");
    throw error;
  }
  return payload;
}

function avatarBox(url, name, className) {
  const box = div(className);
  if (url) {
    const image = document.createElement("img");
    image.src = url;
    image.alt = "";
    image.loading = "lazy";
    box.append(image);
  } else {
    box.textContent = name ? name.slice(0, 1) : "?";
  }
  return box;
}

function rawAvatar(url, name, fallbackClass) {
  if (url) {
    const image = document.createElement("img");
    image.src = url;
    image.alt = "";
    image.loading = "lazy";
    return image;
  }
  const fallback = document.createElement("span");
  fallback.className = fallbackClass || "fallback";
  fallback.textContent = name ? name.slice(0, 1) : "?";
  fallback.setAttribute("aria-hidden", "true");
  return fallback;
}

function specialTotal(specialCounts) {
  if (Array.isArray(specialCounts)) {
    return specialCounts.reduce((sum, item) => sum + (item.count || 0), 0);
  }
  return Object.values(specialCounts || {}).reduce((sum, value) => sum + value, 0);
}

function pad(value) {
  return String(value).padStart(2, "0");
}

function clockText(iso) {
  const date = new Date(iso);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function stampText(iso) {
  const date = new Date(iso);
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function dayKey(iso) {
  const date = new Date(iso);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function dayLabel(key) {
  const today = dayKey(new Date().toISOString());
  const yesterday = dayKey(new Date(Date.now() - 86400000).toISOString());
  if (key === today) return "今天";
  if (key === yesterday) return "昨天";
  return key;
}

/* ---------- 视图路由 ---------- */

function currentView() {
  const hash = window.location.hash.replace("#", "");
  return ["draw", "pool", "records"].includes(hash) ? hash : "draw";
}

function route() {
  const view = currentView();
  el.views.forEach((node) => node.classList.toggle("hidden", node.dataset.view !== view));
  el.navItems.forEach((node) => {
    const on = node.dataset.nav === view;
    node.classList.toggle("is-active", on);
    if (on) node.setAttribute("aria-current", "page");
    else node.removeAttribute("aria-current");
  });
  if (view === "pool" && state.ready) renderPool();
  if (view === "records" && state.ready) {
    if (state.profileStale || !state.profile) {
      loadProfile().catch((error) => {
        el.recordsList.replaceChildren(errorBlock(error.message));
      });
    } else {
      renderRecords();
    }
  }
  window.scrollTo({ top: 0, behavior: "auto" });
}

/* ---------- 结果行卡 ---------- */

function sideBlock(side, name, url) {
  const wrap = div(`side side--${side}`);
  wrap.append(avatarBox(url, name, "side__avatar"));
  wrap.append(span("side__name", name));
  wrap.append(span("side__role", SIDE_LABEL[side]));
  return wrap;
}

function cookingBadge(label) {
  const badge = span("badge-cook");
  badge.insertAdjacentHTML("afterbegin", SVG.star);
  badge.append(span(null, label));
  return badge;
}

function cookingStrip(result) {
  const strip = document.createElement("p");
  strip.className = "cook-strip";
  strip.append(textNode("快来做"));
  strip.append(span("a", result.akito_name));
  strip.append(span("x", "×"));
  strip.append(span("t", result.toya_name));
  strip.append(textNode("的饭吧！"));
  return strip;
}

function buildPairRow(result) {
  const isCook = Boolean(result.is_cooking);
  const article = document.createElement("article");
  article.className = `row${isCook ? " row--cook" : ""}`;

  const main = div("row__main");
  const index = div("row__index");
  index.append(span("row__num", `#${result.position}`));
  index.append(span("row__mode", MODE_LABEL[state.lastMode] || MODE_LABEL.none));

  const pair = div("row__pair");
  pair.append(sideBlock("akito", result.akito_name, result.akito_avatar_url));
  const cross = span("row__cross");
  cross.innerHTML = SVG.cross;
  cross.setAttribute("aria-hidden", "true");
  pair.append(cross);
  pair.append(sideBlock("toya", result.toya_name, result.toya_avatar_url));

  const slot = div("row__slot");
  if (isCook) slot.append(cookingBadge("该做饭啦！"));

  main.append(index, pair, slot);
  article.append(main);
  if (isCook) article.append(cookingStrip(result));
  return article;
}

function buildSpecialRow(result) {
  const article = document.createElement("article");
  article.className = "row row--special";

  const main = div("row__main");
  const index = div("row__index");
  index.append(span("row__num", `#${result.position}`));

  const body = div("special-body");
  const icons = div("special-icons");
  const urls = result.special_asset_urls || [];
  if (urls.length) {
    urls.forEach((url) => icons.append(rawAvatar(url, result.special_label, "fallback")));
  } else {
    icons.append(rawAvatar(null, result.special_label, "fallback"));
  }
  const info = div("special-text");
  const label = document.createElement("strong");
  label.textContent = result.special_label;
  const message = document.createElement("p");
  message.textContent = result.special_message;
  info.append(label, message);
  body.append(icons, info);

  const slot = div("row__slot");
  if (result.counts_as_cooking) slot.append(cookingBadge("计入做饭"));

  main.append(index, body, slot);
  article.append(main);
  return article;
}

function buildRow(result) {
  return result.special_type ? buildSpecialRow(result) : buildPairRow(result);
}

function emptyResults(message) {
  el.results.className = "result-list empty-state";
  const icon = div("empty-state__icon");
  icon.innerHTML = SVG.spark30;
  const text = document.createElement("p");
  text.textContent = message;
  el.results.replaceChildren(icon, text);
}

function renderResults(results) {
  el.results.className = "result-list";
  el.results.replaceChildren(...results.map(buildRow));
}

/* ---------- 固定派生选择器 ---------- */

function pickerOptions() {
  if (state.fixedSide === "none" || !state.catalog) return [];
  return state.catalog[state.fixedSide] || [];
}

function pickerMatches() {
  const query = el.pickerSearch.value.trim().toLowerCase();
  const all = pickerOptions();
  if (!query) return all;
  return all.filter((item) => item.name.toLowerCase().includes(query));
}

function isPickerOpen() {
  return !el.pickerPanel.classList.contains("hidden");
}

function updatePickerTrigger() {
  if (state.fixedSide === "none") {
    el.pickerAvatar.classList.add("hidden");
    el.pickerAvatar.replaceChildren();
    el.pickerName.textContent = "先选「固定彰人」或「固定冬弥」";
    return;
  }
  el.pickerAvatar.classList.remove("hidden");
  const entry = pickerOptions().find((item) => item.name === state.fixedName) || pickerOptions()[0];
  el.pickerAvatar.replaceChildren();
  if (entry) {
    if (entry.avatar_url) {
      const image = document.createElement("img");
      image.src = entry.avatar_url;
      image.alt = "";
      el.pickerAvatar.append(image);
    } else {
      el.pickerAvatar.textContent = entry.name.slice(0, 1);
    }
    el.pickerName.textContent = entry.name;
  }
}

function optionNode(item, index) {
  const li = document.createElement("li");
  li.className = "picker__option";
  li.id = `picker-option-${index}`;
  li.setAttribute("role", "option");
  const selected = item.name === state.fixedName;
  li.setAttribute("aria-selected", String(selected));
  if (index === state.activeIndex) li.classList.add("is-active");

  const bar = span("picker__option-bar");
  bar.setAttribute("aria-hidden", "true");
  li.append(bar);
  li.append(rawAvatar(item.avatar_url, item.name, "picker__option-fallback"));
  li.append(span("picker__option-name", item.name));
  if (selected) {
    const check = span("picker__option__check");
    check.innerHTML = SVG.check;
    check.setAttribute("aria-hidden", "true");
    li.append(check);
  }

  li.addEventListener("mousedown", (event) => event.preventDefault());
  li.addEventListener("click", () => commitPicker(item.name));
  return li;
}

function renderPickerList() {
  if (state.fixedSide === "none") {
    el.pickerList.replaceChildren();
    return;
  }
  const matches = pickerMatches();
  const all = pickerOptions();
  el.pickerClear.classList.toggle("hidden", !el.pickerSearch.value);

  if (!matches.length) {
    el.activeIndex = -1;
    el.pickerSearch.removeAttribute("aria-activedescendant");
    const empty = div("picker__empty");
    const icon = div("picker__empty-icon");
    icon.innerHTML = SVG.search;
    const strong = document.createElement("strong");
    strong.textContent = `没找到「${el.pickerSearch.value.trim()}」`;
    const small = document.createElement("small");
    small.textContent = "换个关键词试试，或者直接选一个";
    empty.append(icon, strong, small);
    el.pickerList.replaceChildren(empty);
    el.pickerHint.textContent = "找到 0 个匹配";
    return;
  }

  if (el.activeIndex < 0 || el.activeIndex >= matches.length) el.activeIndex = 0;
  el.pickerSearch.setAttribute("aria-activedescendant", `picker-option-${el.activeIndex}`);
  el.pickerList.replaceChildren(...matches.map(optionNode));

  const query = el.pickerSearch.value.trim();
  el.pickerHint.textContent = query
    ? `找到 ${matches.length} 个匹配`
    : `共 ${all.length} 个 · 输入名字可以筛选`;
}

function scrollActiveIntoView() {
  const node = el.pickerList.querySelector(".is-active");
  if (node) node.scrollIntoView({ block: "nearest" });
}

function openPicker() {
  if (state.fixedSide === "none" || isPickerOpen()) return;
  el.pickerSearch.value = "";
  el.activeIndex = Math.max(0, pickerOptions().findIndex((item) => item.name === state.fixedName));
  el.pickerPanel.classList.remove("hidden");
  el.pickerTrigger.setAttribute("aria-expanded", "true");
  el.pickerSearch.setAttribute("aria-expanded", "true");
  el.pickerCaret.innerHTML = SVG.caretUp;
  renderPickerList();
  el.pickerSearch.focus();
  scrollActiveIntoView();
}

function closePicker() {
  if (!isPickerOpen()) return;
  el.pickerPanel.classList.add("hidden");
  el.pickerTrigger.setAttribute("aria-expanded", "false");
  el.pickerSearch.setAttribute("aria-expanded", "false");
  el.pickerCaret.innerHTML = SVG.caretDown;
}

function commitPicker(name) {
  state.fixedName = name;
  el.fixedSelect.value = name;
  // 与原生 select 双向同步：change 事件照常在 select 上派发
  el.fixedSelect.dispatchEvent(new Event("change", { bubbles: true }));
  updatePickerTrigger();
  closePicker();
  el.pickerTrigger.focus();
}

function syncFixedSide(side) {
  state.fixedSide = side;
  const enabled = side !== "none";
  el.picker.classList.toggle("is-disabled", !enabled);
  el.pickerTrigger.disabled = !enabled;
  el.fixedSelect.disabled = !enabled;
  el.fixedLabel.textContent = enabled ? `固定${SIDE_LABEL[side]}派生` : "固定派生";
  if (!enabled) closePicker();

  const options = pickerOptions();
  el.fixedSelect.replaceChildren(
    ...options.map((item) => {
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent = item.name;
      return option;
    }),
  );
  const keep = options.some((item) => item.name === state.fixedName);
  state.fixedName = keep ? state.fixedName : (options[0]?.name ?? null);
  if (state.fixedName) el.fixedSelect.value = state.fixedName;
  el.activeIndex = Math.max(0, options.findIndex((item) => item.name === state.fixedName));
  el.pickerSearch.placeholder = `搜索${SIDE_LABEL[side] || ""}派生…`;
  updatePickerTrigger();
  renderPickerList();
}

/* ---------- 抽取 ---------- */

function updateSummary(summary) {
  const draws = summary.draw_count ?? 0;
  const cooking = summary.cooking_count ?? 0;
  const special = specialTotal(summary.special_counts);
  el.metricDraws.textContent = draws;
  el.metricCooking.textContent = cooking;
  el.metricSpecial.textContent = special;
  el.headerCount.textContent = `累计 ${draws} 抽`;
  el.statDraws.textContent = draws;
  el.statCooking.textContent = cooking;
  el.statSpecial.textContent = special;
  el.statNormal.textContent = Math.max(0, draws - special);
  return { draws, cooking, special };
}

async function submitDraw(event) {
  event.preventDefault();
  const fixedSide = state.fixedSide;
  if (fixedSide !== "none" && !state.fixedName) {
    el.drawStatus.textContent = `请先选择一个固定${SIDE_LABEL[fixedSide]}派生`;
    return;
  }
  const payload = {
    count: Number(new FormData(el.form).get("count") || 1),
    fixed_side: fixedSide,
    fixed_name: fixedSide === "none" ? null : state.fixedName,
  };
  el.drawButton.disabled = true;
  el.drawStatus.textContent = "正在抽取…";
  try {
    const data = await api("/api/v1/draw", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    state.lastMode = fixedSide;
    state.lastBatchAt = data.created_at;
    renderResults(data.results);
    el.resultTime.textContent = stampText(data.created_at);
    updateSummary(data.summary);
    state.profileStale = true;
    el.drawStatus.textContent = "";
  } catch (error) {
    el.drawStatus.textContent = error.retryAfter
      ? `${error.message}（约 ${error.retryAfter} 秒）`
      : error.message;
  } finally {
    el.drawButton.disabled = false;
  }
}

/* ---------- 派生池 ---------- */

function poolCounts() {
  const akito = state.catalog?.akito?.length ?? 0;
  const toya = state.catalog?.toya?.length ?? 0;
  const special = state.catalog?.special_outcomes?.length ?? 0;
  return { akito, toya, special, all: akito + toya };
}

function buildPoolFilters() {
  const counts = poolCounts();
  const defs = [
    ["all", "全部", counts.all],
    ["akito", "彰人", counts.akito],
    ["toya", "冬弥", counts.toya],
    ["special", "特殊", counts.special],
  ];
  el.poolFilters.replaceChildren(
    ...defs.map(([value, label, count]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `filter-chip${state.poolFilter === value ? " is-active" : ""}`;
      button.append(span(null, label));
      button.append(span("filter-chip__count", String(count)));
      button.addEventListener("click", () => {
        state.poolFilter = value;
        state.poolPage = 1;
        buildPoolFilters();
        renderPool();
      });
      return button;
    }),
  );
}

function poolMatches() {
  const query = state.poolSearch.trim().toLowerCase();
  const match = (name) => !query || String(name).toLowerCase().includes(query);
  const akito = (state.catalog?.akito || []).filter((item) => match(item.name));
  const toya = (state.catalog?.toya || []).filter((item) => match(item.name));
  const special = (state.catalog?.special_outcomes || []).filter((item) => match(item.label));
  return { akito, toya, special };
}

function poolCard(item) {
  const card = document.createElement("article");
  card.className = "pool-card";
  card.append(avatarBox(item.avatar_url, item.name, "pool-card__avatar"));
  const name = div("pool-card__name");
  name.textContent = item.name;
  card.append(name);
  return card;
}

function sectionHead({ cls, title, count, link, onToggle }) {
  const head = div("pool-section__head");
  const wrap = div("pool-section__title");
  wrap.append(span(`pool-section__dot dot-${cls}`));
  const heading = document.createElement("h2");
  heading.textContent = title;
  wrap.append(heading);
  wrap.append(span("pool-section__count", count));
  head.append(wrap);
  if (link) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `pool-section__link link-${cls}`;
    button.append(span(null, link));
    button.insertAdjacentHTML("beforeend", SVG.chevron);
    button.addEventListener("click", onToggle);
    head.append(button);
  }
  return head;
}

function renderPoolDesktop(matches) {
  const filter = state.poolFilter;
  const nodes = [];

  ["akito", "toya"].forEach((side) => {
    if (filter !== "all" && filter !== side) return;
    const all = matches[side];
    if (!all.length) return;
    const expanded = state.poolExpanded[side];
    const slice = expanded ? all : all.slice(0, POOL_SECTION_LIMIT);
    const section = document.createElement("section");
    section.className = "pool-section";
    const total = state.catalog[side].length;
    section.append(
      sectionHead({
        cls: side,
        title: `${SIDE_LABEL[side]}派生`,
        count: `${all.length}${all.length === total ? "" : ` / ${total}`} 个`,
        link:
          all.length > POOL_SECTION_LIMIT ? (expanded ? "收起" : `查看全部 ${all.length}`) : null,
        onToggle: () => {
          state.poolExpanded[side] = !expanded;
          renderPool();
        },
      }),
    );
    const grid = div("pool-grid");
    slice.forEach((item) => grid.append(poolCard(item, side)));
    section.append(grid);
    nodes.push(section);
  });

  if (filter === "all" || filter === "special") {
    const specials = matches.special;
    if (specials.length) {
      const section = document.createElement("section");
      section.className = "pool-section";
      const head = div("pool-section__head");
      const wrap = div("pool-section__title");
      wrap.append(span("pool-section__dot dot-special"));
      const heading = document.createElement("h2");
      heading.textContent = "特殊结果";
      wrap.append(heading);
      wrap.append(span("pool-section__count", `合计 8% 概率 · ${specials.length} 种`));
      head.append(wrap);
      const note = span("pool-section__count", "抽中时占掉一个抽取位，不组成配对");
      head.append(note);
      section.append(head);

      const grid = div("pool-grid pool-grid--special");
      specials.forEach((item) => {
        const card = div(`special-card${item.id === "foxbun" ? " is-cook" : ""}`);
        const icons = div("special-card__icons");
        (item.asset_urls || []).forEach((url) => icons.append(rawAvatar(url, item.label, "fallback")));
        if (!(item.asset_urls || []).length) icons.append(rawAvatar(null, item.label, "fallback"));
        const body = div("special-card__body");
        const name = div("special-card__name");
        name.textContent = item.label;
        const message = document.createElement("p");
        message.className = "special-card__msg";
        message.textContent = item.message;
        body.append(name, message);
        card.append(icons, body);
        if (item.id === "foxbun") card.append(span("special-card__tag", "计入做饭"));
        grid.append(card);
      });
      section.append(grid);
      nodes.push(section);
    }
  }

  el.poolPager.classList.add("hidden");
  if (!nodes.length) {
    el.poolSections.replaceChildren(emptyBlock("没有匹配的派生，换个关键词试试。"));
    el.poolNote.textContent = "";
    return;
  }
  el.poolSections.replaceChildren(...nodes);
  const total = matches.akito.length + matches.toya.length + matches.special.length;
  el.poolNote.textContent = state.poolSearch.trim() ? `匹配到 ${total} 个条目` : "";
}

function renderPoolMobile(matches) {
  const filter = state.poolFilter;
  let items;
  if (filter === "akito") items = matches.akito.map((item) => ["akito", item]);
  else if (filter === "toya") items = matches.toya.map((item) => ["toya", item]);
  else if (filter === "special") items = matches.special.map((item) => ["special", item]);
  else {
    items = [
      ...matches.akito.map((item) => ["akito", item]),
      ...matches.toya.map((item) => ["toya", item]),
    ];
  }

  const pages = Math.max(1, Math.ceil(items.length / POOL_PAGE_SIZE));
  state.poolPage = Math.min(Math.max(1, state.poolPage), pages);
  const slice = items.slice((state.poolPage - 1) * POOL_PAGE_SIZE, state.poolPage * POOL_PAGE_SIZE);

  if (!items.length) {
    el.poolSections.replaceChildren(emptyBlock("没有匹配的派生，换个关键词试试。"));
    el.poolPager.classList.add("hidden");
    el.poolNote.textContent = "";
    return;
  }

  const grid = div("pool-grid");
  slice.forEach(([side, item]) => {
    if (side === "special") {
      const card = div(`special-card${item.id === "foxbun" ? " is-cook" : ""}`);
      const icons = div("special-card__icons");
      (item.asset_urls || []).forEach((url) => icons.append(rawAvatar(url, item.label, "fallback")));
      if (!(item.asset_urls || []).length) icons.append(rawAvatar(null, item.label, "fallback"));
      const body = div("special-card__body");
      const name = div("special-card__name");
      name.textContent = item.label;
      const message = document.createElement("p");
      message.className = "special-card__msg";
      message.textContent = item.message;
      body.append(name, message);
      card.append(icons, body);
      grid.append(card);
    } else {
      grid.append(poolCard(item, side));
    }
  });
  el.poolSections.replaceChildren(grid);
  el.poolNote.textContent = `共 ${items.length} 个 · 第 ${state.poolPage} / ${pages} 页`;
  renderPager(pages, state.poolPage);
}

function pagerButton(label, { current = false, disabled = false, onClick, icon = false }) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `pager__btn${current ? " is-current" : ""}`;
  button.disabled = disabled;
  if (icon) button.innerHTML = label;
  else button.textContent = label;
  if (current) button.setAttribute("aria-current", "page");
  if (onClick) button.addEventListener("click", onClick);
  return button;
}

function renderPager(pages, current) {
  const go = (page) => {
    state.poolPage = page;
    renderPool();
    el.poolPager.scrollIntoView({ block: "nearest" });
  };
  const nodes = [
    pagerButton(SVG.chevronLeft, {
      disabled: current <= 1,
      onClick: () => go(current - 1),
      icon: true,
    }),
  ];

  let numbers;
  if (pages <= 7) {
    numbers = Array.from({ length: pages }, (_, index) => index + 1);
  } else {
    numbers = [1, 2];
    if (current > 3) numbers.push("…");
    for (let page = current - 1; page <= current + 1; page += 1) {
      if (page > 2 && page < pages) numbers.push(page);
    }
    if (current < pages - 2) numbers.push("…");
    numbers.push(pages);
    numbers = numbers.filter((value, index, list) => list.indexOf(value) === index);
  }

  numbers.forEach((value) => {
    if (value === "…") {
      const gap = span("pager__gap", "…");
      nodes.push(gap);
      return;
    }
    nodes.push(pagerButton(String(value), { current: value === current, onClick: () => go(value) }));
  });

  nodes.push(
    pagerButton(SVG.chevron, {
      disabled: current >= pages,
      onClick: () => go(current + 1),
      icon: true,
    }),
  );
  el.poolPager.replaceChildren(...nodes);
  el.poolPager.classList.remove("hidden");
}

function renderPool() {
  if (!state.catalog) {
    el.poolSections.replaceChildren(emptyBlock("正在读取派生池…"));
    return;
  }
  const counts = poolCounts();
  el.poolSummary.textContent = `共 ${counts.all} 个派生 · 彰人 ${counts.akito} · 冬弥 ${counts.toya} · 另有 ${counts.special} 种特殊结果`;
  const matches = poolMatches();
  if (mqMobilePool.matches) renderPoolMobile(matches);
  else renderPoolDesktop(matches);
}

function emptyBlock(message) {
  const box = div("records-empty");
  box.textContent = message;
  return box;
}

function errorBlock(message) {
  const box = div("records-empty");
  box.textContent = message;
  return box;
}

/* ---------- 我的记录 ---------- */

function buildRecordFilters() {
  const defs = [
    ["all", "全部"],
    ["cooking", "仅做饭"],
    ["special", "仅特殊"],
  ];
  el.recordFilters.replaceChildren(
    ...defs.map(([value, label]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `filter-chip${state.recordFilter === value ? " is-active" : ""}`;
      button.textContent = label;
      button.addEventListener("click", () => {
        state.recordFilter = value;
        buildRecordFilters();
        renderRecords();
      });
      return button;
    }),
  );
}

function recordRow(entry) {
  const isSpecial = Boolean(entry.special_type);
  const isCook = Boolean(entry.is_cooking || entry.counts_as_cooking);
  const article = document.createElement("article");
  article.className = `record-row${isSpecial ? " record-row--special" : isCook ? " record-row--cook" : ""}`;
  article.append(span("record-row__time", clockText(entry.created_at)));

  const body = div("record-row__body");
  if (isSpecial) {
    const message = span("record-row__msg");
    const strong = document.createElement("strong");
    strong.textContent = entry.special_label;
    message.append(strong, textNode(entry.special_message || ""));
    body.append(message);
  } else {
    body.append(rawAvatar(entry.akito_avatar_url, entry.akito_name, "fallback"));
    body.append(span("record-row__name a", entry.akito_name));
    const cross = span("record-row__cross");
    cross.innerHTML = SVG.cross;
    cross.setAttribute("aria-hidden", "true");
    body.append(cross);
    body.append(rawAvatar(entry.toya_avatar_url, entry.toya_name, "fallback"));
    body.append(span("record-row__name t", entry.toya_name));
  }
  article.append(body);

  const chip = span("outcome-chip", isSpecial ? "特殊结果" : isCook ? "做饭" : "常规组合");
  if (isSpecial) chip.classList.add("is-special");
  else if (isCook) chip.classList.add("is-cook");
  article.append(chip);
  return article;
}

function recordBatchHeader(entry, visibleCount) {
  const header = div("record-batch__head");
  const leftLine = div("record-batch__line");
  const rightLine = div("record-batch__line");
  const requestedCount = Number(entry.requested_count) || visibleCount;
  const title = span("record-batch__title", `本次抽取 · ${requestedCount} 抽`);
  const mode = MODE_LABEL[entry.fixed_side] || MODE_LABEL.none;
  const fixedName = entry.fixed_side !== "none" && entry.fixed_name ? ` · ${entry.fixed_name}` : "";
  const meta = span("record-batch__meta", `${clockText(entry.created_at)} · ${mode}${fixedName}`);
  if (requestedCount !== visibleCount) {
    meta.textContent += ` · 显示 ${visibleCount} 条`;
  }
  header.append(leftLine, title, meta, rightLine);
  return header;
}

function renderRecords() {
  const profile = state.profile;
  if (!profile) return;
  const recent = profile.recent || [];
  const filtered = recent.filter((entry) => {
    if (state.recordFilter === "cooking") return entry.is_cooking || entry.counts_as_cooking;
    if (state.recordFilter === "special") return Boolean(entry.special_type);
    return true;
  });

  if (!filtered.length) {
    el.recordsList.replaceChildren(
      emptyBlock(recent.length ? "这个筛选下还没有记录。" : "还没有抽取记录，去抽一次吧。"),
    );
    return;
  }

  const groups = new Map();
  filtered.forEach((entry) => {
    const key = dayKey(entry.created_at);
    if (!groups.has(key)) groups.set(key, new Map());
    const batches = groups.get(key);
    const batchKey = entry.batch_id || `${entry.created_at}:${entry.requested_count || 1}`;
    if (!batches.has(batchKey)) batches.set(batchKey, []);
    batches.get(batchKey).push(entry);
  });

  el.recordsList.replaceChildren(
    ...Array.from(groups.entries()).map(([key, batches]) => {
      const entries = Array.from(batches.values()).flat();
      const group = div("day-group");
      const head = div("day-head");
      const left = document.createElement("strong");
      left.textContent = `${dayLabel(key)} · ${key}`;
      const parts = [`${entries.length} 抽`];
      const cook = entries.filter((entry) => entry.is_cooking || entry.counts_as_cooking).length;
      const special = entries.filter((entry) => entry.special_type).length;
      if (cook) parts.push(`${cook} 次做饭`);
      if (special) parts.push(`${special} 次特殊结果`);
      const right = document.createElement("small");
      right.textContent = parts.join(" · ");
      head.append(left, right);
      const batchBlocks = Array.from(batches.values()).map((batchEntries) => {
        const batch = div("record-batch");
        batch.append(recordBatchHeader(batchEntries[0], batchEntries.length));
        batch.append(...batchEntries.map(recordRow));
        return batch;
      });
      group.append(head, ...batchBlocks);
      return group;
    }),
  );
}

async function loadProfile() {
  const profile = await api("/api/v1/me");
  state.profile = profile;
  state.profileStale = false;
  updateSummary(profile);
  buildRecordFilters();
  renderRecords();
}

async function clearHistory(event) {
  if (event.currentTarget.value !== "confirm") return;
  try {
    await api("/api/v1/me/history", { method: "DELETE" });
    emptyResults("记录已清除，随时可以重新开始。");
    el.resultTime.textContent = "还没有抽取";
    state.lastBatchAt = null;
    state.profileStale = true;
    await loadProfile();
  } catch (error) {
    el.drawStatus.textContent = error.message;
  }
}

/* ---------- 事件绑定 ---------- */

function bindEvents() {
  el.form.addEventListener("change", (event) => {
    if (event.target.name === "fixed_side") syncFixedSide(event.target.value);
  });
  el.form.addEventListener("submit", submitDraw);

  el.pickerTrigger.addEventListener("click", () => {
    if (isPickerOpen()) closePicker();
    else openPicker();
  });
  el.pickerTrigger.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      openPicker();
    }
  });
  el.pickerSearch.addEventListener("input", () => {
    el.activeIndex = 0;
    renderPickerList();
    scrollActiveIntoView();
  });
  el.pickerSearch.addEventListener("keydown", (event) => {
    const matches = pickerMatches();
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!matches.length) return;
      const step = event.key === "ArrowDown" ? 1 : -1;
      el.activeIndex = (el.activeIndex + step + matches.length) % matches.length;
      renderPickerList();
      scrollActiveIntoView();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const item = matches[el.activeIndex];
      if (item) commitPicker(item.name);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closePicker();
      el.pickerTrigger.focus();
    }
  });
  el.pickerClear.addEventListener("click", () => {
    el.pickerSearch.value = "";
    el.activeIndex = 0;
    renderPickerList();
    el.pickerSearch.focus();
  });
  document.addEventListener("pointerdown", (event) => {
    if (isPickerOpen() && !el.picker.contains(event.target) && !el.pickerPanel.contains(event.target)) {
      closePicker();
    }
  });

  el.poolSearch.addEventListener("input", () => {
    state.poolSearch = el.poolSearch.value;
    state.poolPage = 1;
    renderPool();
  });

  el.navItems.forEach((node) => {
    node.addEventListener("click", () => {
      const view = node.dataset.nav;
      if (window.location.hash === `#${view}`) route();
      else window.location.hash = view;
    });
  });
  window.addEventListener("hashchange", route);
  mqMobilePool.addEventListener("change", () => {
    state.poolPage = 1;
    renderPool();
  });

  el.clearButton.addEventListener("click", () => el.clearDialog.showModal());
  el.confirmClear.addEventListener("click", clearHistory);
}

/* ---------- 启动 ---------- */

async function initialize() {
  document.documentElement.classList.remove("no-js");
  el.fixedSelect.setAttribute("tabindex", "-1");
  el.fixedSelect.setAttribute("aria-hidden", "true");
  el.pickerCaret.innerHTML = SVG.caretDown;
  bindEvents();
  route();

  try {
    await api("/api/v1/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    state.catalog = await api("/api/v1/catalog");
  } catch (error) {
    el.drawStatus.textContent = `加载失败：${error.message}`;
    return;
  }

  state.ready = true;
  buildPoolFilters();
  syncFixedSide("none");
  renderPool();
  await loadProfile();
  route();
}

initialize().catch((error) => {
  el.drawStatus.textContent = `加载失败：${error.message}`;
});
