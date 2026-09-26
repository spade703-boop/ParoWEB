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
const RECORD_EXPORT_LIMIT = 12;
const RECORD_PAGE_SIZE = 50;

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
  lastDraw: null,
  poolFilter: "all",
  poolSearch: "",
  poolPage: 1,
  poolExpanded: { akito: false, toya: false },
  recordFilter: "all",
  announcements: null,
  communityStats: null,
  communityStatsScope: "community",
  communityStatsLoading: false,
  recentLoading: false,
  recordSelectionMode: false,
  selectedRecordIds: new Set(),
  exportResult: null,
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
  exportCurrentButton: $("#export-current-button"),
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
  homeUpdate: $("#home-update"),
  homeUpdateDate: $("#home-update-date"),
  homeUpdateTitle: $("#home-update-title"),
  updatesList: $("#updates-list"),
  communityTotal: $("#community-total-draws"),
  communityTotalLabel: $("#community-total-label"),
  statsScopeButtons: $$('[data-stats-scope]'),
  communityAkitoList: $("#community-akito-list"),
  communityToyaList: $("#community-toya-list"),
  communityPairList: $("#community-pair-list"),
  statDraws: $("#stat-draws"),
  statCooking: $("#stat-cooking"),
  statSpecial: $("#stat-special"),
  statNormal: $("#stat-normal"),
  recordFilters: $("#record-filters"),
  recordsList: $("#records-list"),
  recordsPager: $("#records-pager"),
  recordsPageStatus: $("#records-page-status"),
  recordsLoadMore: $("#records-load-more"),
  recordSelectButton: $("#record-select-button"),
  recordSelectLabel: $("#record-select-label"),
  recordExportBar: $("#record-export-bar"),
  recordExportCount: $("#record-export-count"),
  recordExportStatus: $("#record-export-status"),
  recordExportCancel: $("#record-export-cancel"),
  recordExportGenerate: $("#record-export-generate"),
  clearButton: $("#clear-button"),
  clearDialog: $("#clear-dialog"),
  confirmClear: $("#confirm-clear"),
  exportDialog: $("#export-dialog"),
  exportDialogClose: $("#export-dialog-close"),
  exportPreview: $("#export-preview"),
  exportDialogStatus: $("#export-dialog-status"),
  exportDownloadButton: $("#export-download-button"),
  exportShareButton: $("#export-share-button"),
  updateDialog: $("#update-dialog"),
  updateDialogClose: $("#update-dialog-close"),
  updateDialogTitle: $("#update-dialog-title"),
  updateDialogDate: $("#update-dialog-date"),
  updateDialogSummary: $("#update-dialog-summary"),
  updateDialogDetails: $("#update-dialog-details"),
  updateDialogTags: $("#update-dialog-tags"),
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

function recordBatchKey(entry) {
  return entry.batch_id || `${entry.created_at}:${entry.requested_count || 1}`;
}

/* ---------- 视图路由 ---------- */

function currentView() {
  const hash = window.location.hash.replace("#", "");
  return ["draw", "pool", "stats", "records", "updates"].includes(hash) ? hash : "draw";
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
  if (view === "stats" && state.ready) {
    if (state.communityStats) renderCommunityStats();
    else loadCommunityStats();
  }
  if (view === "records" && state.ready) {
    if (state.profileStale || !state.profile) {
      loadProfile().catch((error) => {
        el.recordsList.replaceChildren(errorBlock(error.message));
      });
    } else {
      renderRecords();
    }
  }
  if (view === "updates" && state.ready) {
    if (state.announcements === null) {
      loadAnnouncements().catch(() => {
        state.announcements = [];
        renderAnnouncements("更新公告暂时无法加载，请稍后刷新重试。");
      });
    } else {
      renderAnnouncements();
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
    state.lastDraw = {
      batchId: data.batch_id,
      createdAt: data.created_at,
      requestedCount: payload.count,
      fixedSide,
      fixedName: payload.fixed_name,
      items: data.results,
    };
    renderResults(data.results);
    el.resultTime.textContent = stampText(data.created_at);
    el.exportCurrentButton.classList.remove("hidden");
    updateSummary(data.summary);
    state.profileStale = true;
    state.communityStats = null;
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

/* ---------- 更新公告 ---------- */

function announcementCard(item) {
  const article = document.createElement("article");
  article.className = `update-card${item.highlight ? " is-highlight" : ""}`;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "update-card__button";

  const head = div("update-card__head");
  const date = span("update-card__date", item.published_at);
  if (item.highlight) head.append(span("update-card__latest", "最新"));
  head.append(date);

  const title = document.createElement("h2");
  title.textContent = item.title;
  const summary = document.createElement("p");
  summary.textContent = item.summary;
  const footer = div("update-card__footer");
  const tags = div("update-card__tags");
  (item.tags || []).forEach((tag) => tags.append(span("update-tag", tag)));
  const arrow = span("update-card__arrow");
  arrow.innerHTML = SVG.chevron;
  footer.append(tags, arrow);
  button.append(head, title, summary, footer);
  button.addEventListener("click", () => openAnnouncement(item));
  article.append(button);
  return article;
}

function renderHomeAnnouncement() {
  if (!el.homeUpdate) return;
  const latest = state.announcements?.[0];
  if (!latest) {
    el.homeUpdate.classList.add("hidden");
    return;
  }
  el.homeUpdateDate.textContent = latest.published_at;
  el.homeUpdateDate.dateTime = latest.published_at;
  el.homeUpdateTitle.textContent = latest.title;
  el.homeUpdate.classList.remove("hidden");
}

function renderAnnouncements(message = null) {
  if (!el.updatesList) return;
  renderHomeAnnouncement();
  if (message) {
    el.updatesList.replaceChildren(errorBlock(message));
    return;
  }
  const items = state.announcements || [];
  if (!items.length) {
    el.updatesList.replaceChildren(emptyBlock("暂时还没有更新公告。"));
    return;
  }
  el.updatesList.replaceChildren(...items.map(announcementCard));
}

function openAnnouncement(item) {
  el.updateDialogTitle.textContent = item.title;
  el.updateDialogDate.textContent = item.published_at;
  el.updateDialogSummary.textContent = item.summary;
  el.updateDialogDetails.replaceChildren(
    ...(item.details || []).map((detail) => {
      const node = document.createElement("li");
      node.textContent = detail;
      return node;
    }),
  );
  el.updateDialogTags.replaceChildren(
    ...(item.tags || []).map((tag) => span("update-tag", tag)),
  );
  el.updateDialog.showModal();
}

async function loadAnnouncements() {
  const payload = await api("/api/v1/announcements");
  state.announcements = Array.isArray(payload.items) ? payload.items : [];
  renderAnnouncements();
}

function communityRankRow(item, position, kind) {
  const row = document.createElement("li");
  row.className = `community-rank-row community-rank-row--${kind}`;
  row.append(span("community-rank-row__position", String(position).padStart(2, "0")));

  const body = div("community-rank-row__body");
  if (kind === "pair") {
    const avatars = div("community-rank-row__avatars");
    avatars.append(
      avatarBox(item.akito_avatar_url, item.akito_name, "community-rank-row__avatar community-rank-row__avatar--akito"),
      avatarBox(item.toya_avatar_url, item.toya_name, "community-rank-row__avatar community-rank-row__avatar--toya"),
    );
    const names = div("community-rank-row__names");
    names.append(
      span("community-rank-row__name community-rank-row__name--akito", item.akito_name),
      span("community-rank-row__cross", "×"),
      span("community-rank-row__name community-rank-row__name--toya", item.toya_name),
    );
    body.append(avatars, names);
  } else {
    body.append(
      avatarBox(item.avatar_url, item.name, `community-rank-row__avatar community-rank-row__avatar--${kind}`),
      span("community-rank-row__name", item.name),
    );
  }
  body.append(span("community-rank-row__count", `${item.count} 次`));
  row.append(body);
  return row;
}

function renderCommunityRankList(list, items, kind) {
  if (!list) return;
  if (!items?.length) {
    list.replaceChildren(emptyBlock("暂时还没有抽取数据。"));
    return;
  }
  list.replaceChildren(...items.map((item, index) => communityRankRow(item, index + 1, kind)));
}

function renderCommunityStats(message = null) {
  if (!el.communityTotal) return;
  const personal = state.communityStatsScope === "personal";
  el.communityTotalLabel.textContent = personal ? "我的总抽取数" : "全站总抽取数";
  el.communityTotal.parentElement?.setAttribute("aria-label", personal ? "我的总抽取数" : "全站总抽取数");
  if (message || !state.communityStats) {
    el.communityTotal.textContent = "—";
    const error = errorBlock(message || "正在读取数据…");
    el.communityAkitoList.replaceChildren(error.cloneNode(true));
    el.communityToyaList.replaceChildren(error.cloneNode(true));
    el.communityPairList.replaceChildren(error);
    return;
  }
  el.communityTotal.textContent = Number(state.communityStats.total_draws || 0).toLocaleString("zh-CN");
  renderCommunityRankList(el.communityAkitoList, state.communityStats.akito_top, "akito");
  renderCommunityRankList(el.communityToyaList, state.communityStats.toya_top, "toya");
  renderCommunityRankList(el.communityPairList, state.communityStats.pair_top, "pair");
}

async function loadCommunityStats() {
  if (state.communityStatsLoading) return;
  state.communityStatsLoading = true;
  renderCommunityStats();
  const path = state.communityStatsScope === "personal" ? "/api/v1/me/stats" : "/api/v1/community-stats";
  try {
    state.communityStats = await api(path);
    renderCommunityStats();
  } catch (error) {
    state.communityStats = null;
    renderCommunityStats(error.message || "数据暂时无法加载，请稍后重试。");
  } finally {
    state.communityStatsLoading = false;
  }
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

function setRecordExportStatus(message = "可单独勾选，也可整批选择。") {
  el.recordExportStatus.textContent = message;
}

function updateRecordExportControls() {
  const count = state.selectedRecordIds.size;
  el.recordExportCount.textContent = count;
  el.recordExportGenerate.disabled = count === 0;
  el.recordExportBar.classList.toggle("hidden", !state.recordSelectionMode);
  el.recordSelectLabel.textContent = state.recordSelectionMode ? "退出选择" : "选择导出";
  el.clearButton.classList.toggle("hidden", state.recordSelectionMode);
}

function setRecordSelectionMode(enabled) {
  state.recordSelectionMode = enabled;
  if (!enabled) state.selectedRecordIds.clear();
  setRecordExportStatus();
  updateRecordExportControls();
  renderRecords();
}

function updateSelectedRecords(entries, checked) {
  const ids = entries.map((entry) => entry.id).filter(Boolean);
  if (checked) {
    const additions = ids.filter((id) => !state.selectedRecordIds.has(id));
    if (state.selectedRecordIds.size + additions.length > RECORD_EXPORT_LIMIT) {
      setRecordExportStatus(`一张图片最多选择 ${RECORD_EXPORT_LIMIT} 条，请先取消部分记录。`);
      renderRecords();
      return false;
    }
    additions.forEach((id) => state.selectedRecordIds.add(id));
  } else {
    ids.forEach((id) => state.selectedRecordIds.delete(id));
  }
  setRecordExportStatus();
  renderRecords();
  return true;
}

function recordSelectionControl({ checked, indeterminate = false, label, onChange }) {
  const wrapper = document.createElement("label");
  wrapper.className = "record-select";
  wrapper.title = label;
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = checked;
  input.indeterminate = indeterminate;
  input.setAttribute("aria-label", label);
  input.addEventListener("change", () => {
    const accepted = onChange(input.checked);
    if (accepted === false) input.checked = false;
  });
  wrapper.append(input, span("record-select__mark"));
  return wrapper;
}

function recordRow(entry) {
  const isSpecial = Boolean(entry.special_type);
  const isCook = Boolean(entry.is_cooking || entry.counts_as_cooking);
  const article = document.createElement("article");
  const selected = state.selectedRecordIds.has(entry.id);
  article.className = `record-row${isSpecial ? " record-row--special" : isCook ? " record-row--cook" : ""}${selected ? " is-selected" : ""}`;
  if (state.recordSelectionMode) {
    article.append(
      recordSelectionControl({
        checked: selected,
        label: `选择 ${clockText(entry.created_at)} 的记录`,
        onChange: (checked) => updateSelectedRecords([entry], checked),
      }),
    );
  }
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

function recordBatchHeader(entry, visibleEntries, allEntries) {
  const header = div("record-batch__head");
  const leftLine = div("record-batch__line");
  const rightLine = div("record-batch__line");
  const visibleCount = visibleEntries.length;
  const requestedCount = Number(entry.requested_count) || visibleCount;
  const title = span("record-batch__title", `本次抽取 · ${requestedCount} 抽`);
  const mode = MODE_LABEL[entry.fixed_side] || MODE_LABEL.none;
  const fixedName = entry.fixed_side !== "none" && entry.fixed_name ? ` · ${entry.fixed_name}` : "";
  const meta = span("record-batch__meta", `${clockText(entry.created_at)} · ${mode}${fixedName}`);
  if (requestedCount !== visibleCount) {
    meta.textContent += ` · 显示 ${visibleCount} 条`;
  }
  if (state.recordSelectionMode) {
    const selectedCount = allEntries.filter((item) => state.selectedRecordIds.has(item.id)).length;
    header.append(
      recordSelectionControl({
        checked: selectedCount === allEntries.length && allEntries.length > 0,
        indeterminate: selectedCount > 0 && selectedCount < allEntries.length,
        label: "选择本次抽取",
        onChange: (checked) => updateSelectedRecords(allEntries, checked),
      }),
    );
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
  const allBatches = new Map();
  recent.forEach((entry) => {
    const key = recordBatchKey(entry);
    if (!allBatches.has(key)) allBatches.set(key, []);
    allBatches.get(key).push(entry);
  });

  if (!filtered.length) {
    el.recordsList.replaceChildren(
      emptyBlock(recent.length ? "这个筛选下还没有记录。" : "还没有抽取记录，去抽一次吧。"),
    );
    updateRecordExportControls();
    updateRecordsPager();
    return;
  }

  const groups = new Map();
  filtered.forEach((entry) => {
    const key = dayKey(entry.created_at);
    if (!groups.has(key)) groups.set(key, new Map());
    const batches = groups.get(key);
    const batchKey = recordBatchKey(entry);
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
        const batchKey = recordBatchKey(batchEntries[0]);
        batch.append(recordBatchHeader(batchEntries[0], batchEntries, allBatches.get(batchKey) || batchEntries));
        batch.append(...batchEntries.map(recordRow));
        return batch;
      });
      group.append(head, ...batchBlocks);
      return group;
    }),
  );
  updateRecordExportControls();
  updateRecordsPager();
}

function exportBatch(entries) {
  const first = entries[0];
  return {
    batchId: recordBatchKey(first),
    createdAt: first.created_at,
    requestedCount: Number(first.requested_count) || entries.length,
    fixedSide: first.fixed_side || "none",
    fixedName: first.fixed_name || null,
    items: entries,
  };
}

function currentExportPayload() {
  if (!state.lastDraw) return null;
  return {
    kind: "current",
    title: "本次抽取",
    subtitle: `${state.lastDraw.items.length} 条结果 · ${formatExportSummary(state.lastDraw)}`,
    generatedAt: new Date().toISOString(),
    batches: [state.lastDraw],
  };
}

function historyExportPayload() {
  const selected = (state.profile?.recent || []).filter((entry) => state.selectedRecordIds.has(entry.id));
  if (!selected.length) return null;
  const batches = new Map();
  selected.forEach((entry) => {
    const key = recordBatchKey(entry);
    if (!batches.has(key)) batches.set(key, []);
    batches.get(key).push(entry);
  });
  return {
    kind: "history",
    title: "我的抽取记录",
    subtitle: `${selected.length} 条记录 · ${batches.size} 次抽取`,
    generatedAt: new Date().toISOString(),
    batches: Array.from(batches.values(), exportBatch),
  };
}

function updateRecordsPager() {
  if (!el.recordsPager) return;
  const total = Number(state.profile?.recent_total) || 0;
  const loaded = state.profile?.recent?.length || 0;
  const hasMore = Boolean(state.profile?.recent_has_more);
  el.recordsPager.classList.toggle("hidden", total === 0);
  el.recordsPageStatus.textContent = total ? `已显示 ${loaded} / ${total} 条记录` : "";
  el.recordsLoadMore.classList.toggle("hidden", !hasMore);
  el.recordsLoadMore.disabled = state.recentLoading;
  el.recordsLoadMore.textContent = state.recentLoading ? "正在加载…" : "加载更早记录";
}

function formatExportSummary(batch) {
  const mode = MODE_LABEL[batch.fixedSide] || MODE_LABEL.none;
  const fixedName = batch.fixedSide !== "none" && batch.fixedName ? ` · ${batch.fixedName}` : "";
  return `${stampText(batch.createdAt)} · ${mode}${fixedName}`;
}

function clearExportResult() {
  if (state.exportResult?.previewUrl) URL.revokeObjectURL(state.exportResult.previewUrl);
  state.exportResult = null;
  el.exportPreview.replaceChildren();
  el.exportDialogStatus.textContent = "";
}

function shareFileForResult(result) {
  if (typeof File !== "function") return null;
  return new File([result.blob], result.filename, { type: "image/png" });
}

function canShareResult(result) {
  const file = shareFileForResult(result);
  if (!file || typeof navigator.share !== "function" || typeof navigator.canShare !== "function") return false;
  return navigator.canShare({ files: [file] });
}

function showExportResult(result) {
  clearExportResult();
  result.previewUrl = URL.createObjectURL(result.blob);
  state.exportResult = result;
  const image = document.createElement("img");
  image.src = result.previewUrl;
  image.alt = "导出的抽派生结果预览";
  el.exportPreview.append(image);
  result.canvas.width = 1;
  result.canvas.height = 1;
  el.exportShareButton.classList.toggle("hidden", !canShareResult(result));
  el.exportDialogStatus.textContent = "图片只在当前浏览器中生成，不会上传到服务器。";
  el.exportDialog.showModal();
}

async function generateExport(payload, trigger, statusTarget) {
  if (!payload) return;
  if (!window.ParoImageExporter) {
    statusTarget.textContent = "图片模块加载失败，请刷新页面后重试。";
    return;
  }
  trigger.disabled = true;
  trigger.setAttribute("aria-busy", "true");
  statusTarget.textContent = "正在生成图片，请稍候…";
  try {
    const result = await window.ParoImageExporter.render(payload);
    showExportResult(result);
    statusTarget.textContent = "";
  } catch (error) {
    statusTarget.textContent = error.message || "图片生成失败，请重试。";
  } finally {
    trigger.disabled = false;
    trigger.removeAttribute("aria-busy");
  }
}

function downloadExportResult() {
  const result = state.exportResult;
  if (!result) return;
  const url = URL.createObjectURL(result.blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = result.filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  el.exportDialogStatus.textContent = "已请求保存图片；如果浏览器没有直接下载，可长按预览图保存。";
}

async function shareExportResult() {
  const result = state.exportResult;
  const file = result ? shareFileForResult(result) : null;
  if (!result || !file || !canShareResult(result)) return;
  el.exportShareButton.disabled = true;
  try {
    await navigator.share({
      files: [file],
      title: "抽派生结果",
      text: "来自 akitoya.top 的抽派生结果",
    });
    el.exportDialogStatus.textContent = "分享面板已打开。";
  } catch (error) {
    if (error.name !== "AbortError") {
      el.exportDialogStatus.textContent = "分享失败，可以改用“保存 PNG”。";
    }
  } finally {
    el.exportShareButton.disabled = false;
  }
}

async function loadProfile({ append = false } = {}) {
  if (state.recentLoading) return;
  state.recentLoading = true;
  updateRecordsPager();
  try {
    const offset = append ? state.profile?.recent?.length || 0 : 0;
    const profile = await api(`/api/v1/me?recent_offset=${offset}&recent_limit=${RECORD_PAGE_SIZE}`);
    if (append && state.profile) {
      const knownIds = new Set((state.profile.recent || []).map((entry) => entry.id));
      profile.recent = [
        ...(state.profile.recent || []),
        ...(profile.recent || []).filter((entry) => !knownIds.has(entry.id)),
      ];
    }
    state.profile = profile;
    state.profileStale = false;
    el.recordSelectButton.disabled = !profile.recent?.length;
    updateSummary(profile);
    buildRecordFilters();
    renderRecords();
  } finally {
    state.recentLoading = false;
    updateRecordsPager();
  }
}

async function clearHistory(event) {
  if (event.currentTarget.value !== "confirm") return;
  try {
    await api("/api/v1/me/history", { method: "DELETE" });
    emptyResults("记录已清除，随时可以重新开始。");
    el.resultTime.textContent = "还没有抽取";
    state.lastBatchAt = null;
    state.lastDraw = null;
    el.exportCurrentButton.classList.add("hidden");
    state.recordSelectionMode = false;
    state.selectedRecordIds.clear();
    state.profileStale = true;
    state.communityStats = null;
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
  el.exportCurrentButton.addEventListener("click", () => {
    generateExport(currentExportPayload(), el.exportCurrentButton, el.drawStatus);
  });

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
  el.statsScopeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const scope = button.dataset.statsScope;
      if (!scope || scope === state.communityStatsScope) return;
      state.communityStatsScope = scope;
      state.communityStats = null;
      el.statsScopeButtons.forEach((item) => {
        const active = item.dataset.statsScope === scope;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-selected", String(active));
      });
      loadCommunityStats();
    });
  });
  window.addEventListener("hashchange", route);
  mqMobilePool.addEventListener("change", () => {
    state.poolPage = 1;
    renderPool();
  });

  el.recordSelectButton.addEventListener("click", () => {
    setRecordSelectionMode(!state.recordSelectionMode);
  });
  el.recordExportCancel.addEventListener("click", () => setRecordSelectionMode(false));
  el.recordExportGenerate.addEventListener("click", () => {
    generateExport(historyExportPayload(), el.recordExportGenerate, el.recordExportStatus);
  });
  el.recordsLoadMore.addEventListener("click", () => {
    loadProfile({ append: true }).catch((error) => {
      el.recordsPageStatus.textContent = error.message;
    });
  });
  el.clearButton.addEventListener("click", () => el.clearDialog.showModal());
  el.confirmClear.addEventListener("click", clearHistory);
  el.exportDialogClose.addEventListener("click", () => el.exportDialog.close());
  el.exportDownloadButton.addEventListener("click", downloadExportResult);
  el.exportShareButton.addEventListener("click", shareExportResult);
  el.exportDialog.addEventListener("close", clearExportResult);
  el.updateDialogClose.addEventListener("click", () => el.updateDialog.close());
}

/* ---------- 启动 ---------- */

async function initialize() {
  document.documentElement.classList.remove("no-js");
  el.recordSelectButton.disabled = true;
  updateRecordsPager();
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
    await loadAnnouncements();
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
