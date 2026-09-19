const state = { catalog: null, profile: null, catalogSide: "akito", search: "" };

const elements = {
  form: document.querySelector("#draw-form"),
  drawButton: document.querySelector("#draw-button"),
  drawStatus: document.querySelector("#draw-status"),
  fixedPicker: document.querySelector("#fixed-picker"),
  fixedLabel: document.querySelector("#fixed-label"),
  fixedName: document.querySelector("#fixed-name"),
  results: document.querySelector("#results"),
  totalChip: document.querySelector("#total-chip"),
  catalogGrid: document.querySelector("#catalog-grid"),
  catalogCount: document.querySelector("#catalog-count"),
  catalogSearch: document.querySelector("#catalog-search"),
  clearDialog: document.querySelector("#clear-dialog"),
  clearButton: document.querySelector("#clear-button"),
  confirmClear: document.querySelector("#confirm-clear"),
};

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

function avatar(url, name, side) {
  const wrapper = document.createElement("div");
  wrapper.className = `avatar ${side}`;
  if (url) {
    const image = document.createElement("img");
    image.src = url;
    image.alt = `${name}头像`;
    image.loading = "lazy";
    wrapper.append(image);
  } else {
    const fallback = document.createElement("span");
    fallback.textContent = name?.slice(0, 1) || "?";
    fallback.setAttribute("aria-label", `${name || "派生"}暂无头像`);
    wrapper.append(fallback);
  }
  return wrapper;
}

function selectedValue(name) {
  return new FormData(elements.form).get(name);
}

function updateFixedPicker() {
  const side = selectedValue("fixed_side");
  elements.fixedPicker.classList.toggle("hidden", side === "none");
  if (side === "none" || !state.catalog) return;
  const entries = state.catalog[side];
  elements.fixedLabel.textContent = `选择${side === "akito" ? "彰人" : "冬弥"}派生`;
  elements.fixedName.replaceChildren(...entries.map((item) => {
    const option = document.createElement("option");
    option.value = item.name;
    option.textContent = item.name;
    return option;
  }));
}

function renderResults(results) {
  elements.results.className = "results";
  elements.results.replaceChildren(...results.map((result) => {
    const card = document.createElement("article");
    card.className = `result-card ${result.special_type ? "special-card" : "pair-card"}`;
    const number = document.createElement("span");
    number.className = "result-number";
    number.textContent = `DRAW ${String(result.position).padStart(2, "0")}`;
    card.append(number);
    if (result.special_type) {
      const visual = document.createElement("div");
      visual.className = "special-images";
      if (result.special_asset_urls.length) {
        result.special_asset_urls.forEach((url) => {
          const image = document.createElement("img");
          image.src = url;
          image.alt = result.special_label;
          visual.append(image);
        });
      } else {
        const fallback = document.createElement("span");
        fallback.className = "special-placeholder";
        fallback.textContent = "✦";
        visual.append(fallback);
      }
      const title = document.createElement("h3");
      title.textContent = result.special_label;
      const message = document.createElement("p");
      message.textContent = result.special_message;
      card.append(visual, title, message);
      if (result.counts_as_cooking) {
        const badge = document.createElement("div");
        badge.className = "cooking-badge";
        badge.textContent = "本次计入做饭 ✦";
        card.append(badge);
      }
      return card;
    }
    const visual = document.createElement("div");
    visual.className = "pair-visual";
    visual.append(
      avatar(result.akito_avatar_url, result.akito_name, "akito"),
      avatar(result.toya_avatar_url, result.toya_name, "toya"),
    );
    const names = document.createElement("div");
    names.className = "pair-names";
    names.innerHTML = `<span class="akito-name"></span><span>×</span><span class="toya-name"></span>`;
    names.querySelector(".akito-name").textContent = result.akito_name;
    names.querySelector(".toya-name").textContent = result.toya_name;
    card.append(visual, names);
    if (result.is_cooking) {
      const badge = document.createElement("div");
      badge.className = "cooking-badge";
      badge.textContent = "今天由这对来做饭 ✦";
      card.append(badge);
    }
    return card;
  }));
}

async function submitDraw(event) {
  event.preventDefault();
  elements.drawButton.disabled = true;
  elements.drawStatus.textContent = "正在抽取…";
  const fixedSide = selectedValue("fixed_side");
  const payload = {
    count: Number(selectedValue("count")),
    fixed_side: fixedSide,
    fixed_name: fixedSide === "none" ? null : elements.fixedName.value,
  };
  try {
    const data = await api("/api/v1/draw", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderResults(data.results);
    state.profile = data.summary;
    elements.totalChip.textContent = `累计 ${data.summary.draw_count} 抽`;
    elements.drawStatus.textContent = "";
  } catch (error) {
    elements.drawStatus.textContent = error.retryAfter ? `${error.message}（约 ${error.retryAfter} 秒）` : error.message;
  } finally {
    elements.drawButton.disabled = false;
  }
}

function renderCatalog() {
  if (!state.catalog) return;
  const query = state.search.trim().toLocaleLowerCase();
  const entries = state.catalog[state.catalogSide].filter((item) => item.name.toLocaleLowerCase().includes(query));
  const label = state.catalogSide === "akito" ? "彰人" : "冬弥";
  elements.catalogCount.textContent = `${label}派生 · 显示 ${entries.length} / ${state.catalog[state.catalogSide].length}`;
  elements.catalogGrid.replaceChildren(...entries.map((item) => {
    const card = document.createElement("article");
    card.className = "catalog-card";
    const title = document.createElement("h2");
    title.textContent = item.name;
    const side = document.createElement("small");
    side.textContent = `${label}派生`;
    card.append(avatar(item.avatar_url, item.name, state.catalogSide), title, side);
    return card;
  }));
}

function emptyRank(text = "暂无记录") {
  const item = document.createElement("li");
  item.innerHTML = `<span class="rank-number">—</span><span></span><span class="rank-count">0 次</span>`;
  item.children[1].textContent = text;
  return item;
}

function rankItems(entries, pair = false) {
  if (!entries.length) return [emptyRank()];
  return entries.map((entry, index) => {
    const item = document.createElement("li");
    const rank = document.createElement("span");
    rank.className = "rank-number";
    rank.textContent = String(index + 1).padStart(2, "0");
    const name = document.createElement("span");
    name.textContent = pair ? `${entry.akito_name} × ${entry.toya_name}` : entry.name;
    const count = document.createElement("span");
    count.className = "rank-count";
    count.textContent = `${entry.count} 次`;
    item.append(rank, name, count);
    return item;
  });
}

function formatTime(value) {
  return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function renderProfile(profile) {
  state.profile = profile;
  document.querySelector("#metric-draws").textContent = profile.draw_count;
  document.querySelector("#metric-cooking").textContent = profile.cooking_count;
  elements.totalChip.textContent = `累计 ${profile.draw_count} 抽`;
  document.querySelector("#special-stats").replaceChildren(...profile.special_counts.map((entry) => {
    const pill = document.createElement("span");
    pill.className = "special-pill";
    pill.textContent = `${entry.label} ${entry.count}`;
    return pill;
  }));
  document.querySelector("#akito-top").replaceChildren(...rankItems(profile.akito_top));
  document.querySelector("#toya-top").replaceChildren(...rankItems(profile.toya_top));
  document.querySelector("#pair-top").replaceChildren(...rankItems(profile.pair_top, true));
  const history = document.querySelector("#history-list");
  if (!profile.recent.length) {
    const empty = document.createElement("p");
    empty.className = "empty-history";
    empty.textContent = "还没有抽取记录。";
    history.replaceChildren(empty);
    return;
  }
  history.replaceChildren(...profile.recent.map((entry) => {
    const item = document.createElement("article");
    item.className = "history-item";
    const main = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = entry.special_type ? entry.special_label : `${entry.akito_name} × ${entry.toya_name}`;
    const detail = document.createElement("small");
    const cooking = entry.counts_as_cooking ? " · 做饭" : "";
    detail.textContent = `${formatTime(entry.created_at)}${cooking}`;
    main.append(title, detail);
    const mode = document.createElement("span");
    mode.className = "history-mode";
    mode.textContent = entry.fixed_side === "none" ? "随机" : entry.fixed_side === "akito" ? "固定彰人" : "固定冬弥";
    item.append(main, mode);
    return item;
  }));
}

async function loadProfile() {
  const profile = await api("/api/v1/me");
  renderProfile(profile);
}

async function clearHistory(event) {
  if (event.currentTarget.value !== "confirm") return;
  try {
    await api("/api/v1/me/history", { method: "DELETE" });
    elements.results.className = "results empty-state";
    elements.results.innerHTML = '<div class="empty-symbol" aria-hidden="true">✦</div><p>记录已清除，随时可以重新开始。</p>';
    await loadProfile();
  } catch (error) {
    elements.drawStatus.textContent = error.message;
  }
}

function showPanel(target) {
  document.querySelectorAll("[data-panel]").forEach((panel) => panel.classList.toggle("hidden", panel.dataset.panel !== target));
  document.querySelectorAll(".nav-button").forEach((button) => {
    const active = button.dataset.target === target;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  if (target === "profile") loadProfile().catch((error) => { elements.drawStatus.textContent = error.message; });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function initialize() {
  await api("/api/v1/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  state.catalog = await api("/api/v1/catalog");
  updateFixedPicker();
  renderCatalog();
  await loadProfile();
}

elements.form.addEventListener("change", (event) => { if (event.target.name === "fixed_side") updateFixedPicker(); });
elements.form.addEventListener("submit", submitDraw);
elements.catalogSearch.addEventListener("input", (event) => { state.search = event.target.value; renderCatalog(); });
document.querySelectorAll(".catalog-side").forEach((button) => button.addEventListener("click", () => {
  state.catalogSide = button.dataset.side;
  document.querySelectorAll(".catalog-side").forEach((item) => item.classList.toggle("active", item === button));
  renderCatalog();
}));
document.querySelectorAll(".nav-button").forEach((button) => button.addEventListener("click", () => showPanel(button.dataset.target)));
elements.clearButton.addEventListener("click", () => elements.clearDialog.showModal());
elements.confirmClear.addEventListener("click", clearHistory);

initialize().catch((error) => { elements.drawStatus.textContent = `加载失败：${error.message}`; });

