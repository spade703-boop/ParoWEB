/* ==========================================================================
   MySekai 群友入口 · 页面交互
   只做三件事：密码门、记住 QQ/ID、上传 → 展示两张图。
   所有请求都走同源 /api/v1/mysekai/*，不引任何第三方脚本。
   ========================================================================== */

const $ = (selector, root = document) => root.querySelector(selector);
const MAX_FILE_BYTES = 4 * 1024 * 1024;
const QQ_RE = /^\d{5,12}$/;
const GAME_ID_RE = /^\d{15,25}$/;

/* QQ 号与游戏 ID 成对存在本机 localStorage，最多 10 条，方便下次联想。
   旧版本用 cookie 存单值，初始化时迁移一次后删除。 */
const HISTORY_KEY = "paro_mysekai_history";
const HISTORY_LIMIT = 10;
const LEGACY_COOKIE_QQ = "paro_mysekai_qq";
const LEGACY_COOKIE_GAME_ID = "paro_mysekai_gid";

function digitsOf(value) {
  return String(value || "").replace(/\D/g, "");
}

function readCookie(name) {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const item = part.trim();
    if (item.startsWith(prefix)) return item.slice(prefix.length);
  }
  return "";
}

function deleteCookie(name) {
  document.cookie = `${name}=; Path=/; Max-Age=0; SameSite=Lax`;
}

function readHistory() {
  let raw;
  try {
    raw = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
  } catch {
    return [];
  }
  if (!Array.isArray(raw)) return [];
  return raw
    .map((entry) => ({
      qq: digitsOf(entry && entry.qq),
      // 兼容早期写错键名的记录：写入端统一用 game_user_id。
      gameUserId: digitsOf(
        (entry && (entry.game_user_id || entry.gameUserId)) || ""
      ),
      at: Number(entry && entry.at) || 0,
    }))
    .filter((entry) => entry.qq);
}

function writeHistory(entries) {
  try {
    // 存储层固定使用 game_user_id；内部对象用 gameUserId，转换只在这里做。
    const stored = entries.slice(0, HISTORY_LIMIT).map((entry) => ({
      qq: entry.qq,
      game_user_id: entry.gameUserId,
      at: entry.at,
    }));
    localStorage.setItem(HISTORY_KEY, JSON.stringify(stored));
  } catch {
    // 隐私模式或存储被禁用时静默降级：不保存历史，不影响上传。
  }
}

function migrateLegacyCookies() {
  const qq = digitsOf(readCookie(LEGACY_COOKIE_QQ));
  const gameId = digitsOf(readCookie(LEGACY_COOKIE_GAME_ID));
  if (!qq && !gameId) return;
  if (qq) {
    const entries = readHistory();
    if (!entries.some((entry) => entry.qq === qq)) {
      entries.unshift({ qq, gameUserId: gameId, at: Date.now() });
      writeHistory(entries);
    }
  }
  deleteCookie(LEGACY_COOKIE_QQ);
  deleteCookie(LEGACY_COOKIE_GAME_ID);
}

function rememberEntry(qq, gameUserId) {
  // 按「QQ + 游戏 ID」配对去重：一个 QQ 可以有多个角色，换号上传不能覆盖上一条。
  const entries = readHistory().filter(
    (entry) => !(entry.qq === qq && entry.gameUserId === gameUserId)
  );
  entries.unshift({ qq, gameUserId, at: Date.now() });
  writeHistory(entries);
  renderHistory();
}

function forgetEntry(entry) {
  writeHistory(
    readHistory().filter(
      (item) => !(item.qq === entry.qq && item.gameUserId === entry.gameUserId)
    )
  );
  renderHistory();
}

function applyEntry(entry, { focus = true } = {}) {
  el.qq.value = entry.qq;
  if (entry.gameUserId) el.gameId.value = entry.gameUserId;
  el.uploadStatus.classList.remove("is-ok");
  if (focus) {
    el.qq.focus();
    closeSuggest();
  }
}

function matchingEntries(query) {
  const text = digitsOf(query);
  const entries = readHistory();
  if (!text) return entries;
  return entries.filter(
    (entry) =>
      entry.qq.startsWith(text) || (entry.gameUserId || "").startsWith(text)
  );
}

function openSuggest() {
  const entries = matchingEntries(el.qq.value).slice(0, 6);
  el.qqSuggest.innerHTML = "";
  if (!entries.length) {
    closeSuggest();
    return;
  }
  for (const entry of entries) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "suggest-item";
    item.textContent = entry.gameUserId
      ? `${entry.qq} | ${entry.gameUserId}`
      : entry.qq;
    // mousedown 早于 blur，避免点选时列表先被收起。
    item.addEventListener("mousedown", (event) => {
      event.preventDefault();
      applyEntry(entry);
    });
    el.qqSuggest.appendChild(item);
  }
  // 列表展开时压住芯片：用表单级 class + CSS，比改芯片自身的 class 更抗重排。
  el.uploadForm.classList.add("suggest-open");
  el.qqSuggest.classList.remove("hidden");
}

function closeSuggest() {
  el.qqSuggest.classList.add("hidden");
  el.uploadForm.classList.remove("suggest-open");
  if (readHistory().length) el.recentEntries.classList.remove("hidden");
}

function renderHistory() {
  const entries = readHistory();
  el.recentEntries.innerHTML = "";
  el.qqSuggest.innerHTML = "";

  if (!entries.length) {
    el.recentEntries.classList.add("hidden");
    closeSuggest();
    return;
  }
  el.recentEntries.classList.remove("hidden");

  for (const entry of entries.slice(0, 5)) {
    const chip = document.createElement("span");
    chip.className = "recent-chip";
    chip.tabIndex = 0;
    chip.setAttribute("role", "button");
    chip.title = entry.gameUserId
      ? `填充 QQ ${entry.qq} 与游戏 ID ${entry.gameUserId}`
      : `填充 QQ ${entry.qq}`;

    const label = document.createElement("span");
    label.textContent = entry.gameUserId ? `${entry.qq} | ${entry.gameUserId}` : entry.qq;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "recent-chip__remove";
    remove.setAttribute("aria-label", `删除记录 ${entry.qq}`);
    remove.textContent = "×";

    chip.addEventListener("click", (event) => {
      if (event.target !== remove) applyEntry(entry);
    });
    chip.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        applyEntry(entry);
      }
    });
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      forgetEntry(entry);
    });

    chip.append(label, remove);
    el.recentEntries.appendChild(chip);
  }
}

function restoreInputs() {
  const latest = readHistory()[0];
  if (latest) applyEntry(latest, { focus: false });
}

const state = {
  file: null,
  busy: false,
  lastResult: null,
};

const el = {
  gate: $("#gate"),
  gateCard: $("#gate-card"),
  gateForm: $("#gate-form"),
  gatePassword: $("#gate-password"),
  gateStatus: $("#gate-status"),
  gateSubmit: $("#gate-submit"),

  workspace: $("#workspace"),
  uploadForm: $("#upload-form"),
  qq: $("#qq"),
  gameId: $("#game-id"),
  qqSuggest: $("#qq-suggest"),
  recentEntries: $("#recent-entries"),
  dropZone: $("#drop-zone"),
  dropTitle: $("#drop-title"),
  fileInput: $("#file-input"),
  fileChip: $("#file-chip"),
  fileName: $("#file-name"),
  fileSize: $("#file-size"),
  fileClear: $("#file-clear"),
  uploadStatus: $("#upload-status"),
  uploadSubmit: $("#upload-submit"),

  result: $("#result"),
  resultEmpty: $("#result-empty"),
  resultLoading: $("#result-loading"),
  resultContent: $("#result-content"),
  profileAvatar: $("#profile-avatar"),
  profileAvatarFallback: $("#profile-avatar-fallback"),
  profileName: $("#profile-name"),
  profileId: $("#profile-id"),
  pushBadge: $("#push-badge"),
  watchLine: $("#watch-line"),
  resultMessage: $("#result-message"),
  resultMapFigure: $("#result-map-figure"),
  resultCardFigure: $("#result-card-figure"),
  resultRare: $("#result-rare"),
  resultRareFigure: $("#result-rare-figure"),
  resultBirthday: $("#result-birthday"),
  resultBirthdayFigure: $("#result-birthday-figure"),
  resultMap: $("#result-map"),
  resultCard: $("#result-card"),
  downloadMap: $("#download-map"),
  downloadCard: $("#download-card"),
  resultMeta: $("#result-meta"),
};

const reduceMotion = () =>
  window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const message = payload?.error?.message || "请求失败，请稍后再试";
    const error = new Error(message);
    error.code = payload?.error?.code;
    error.status = response.status;
    throw error;
  }
  return payload;
}

function setStatus(node, message) {
  node.textContent = message || "";
}

function setFormStatus(message, ok = false) {
  setStatus(el.uploadStatus, message);
  el.uploadStatus.classList.toggle("is-ok", Boolean(ok) && Boolean(message));
}

function setButton(button, busy, label) {
  button.disabled = busy;
  button.textContent = label;
  button.setAttribute("aria-busy", busy ? "true" : "false");
}

function showGate(message) {
  el.workspace.classList.add("hidden");
  el.gate.classList.remove("hidden");
  setStatus(el.gateStatus, message || "");
  window.setTimeout(() => el.gatePassword.focus(), 0);
}

function showWorkspace() {
  el.gate.classList.add("hidden");
  el.workspace.classList.remove("hidden");
  setStatus(el.gateStatus, "");
  window.setTimeout(() => {
    // 进入页面时自动聚焦输入框，但不弹出建议——只有用户主动点开才显示。
    suppressSuggestOnFocus = true;
    el.qq.focus();
    suppressSuggestOnFocus = false;
  }, 0);
}

function shakeGate() {
  if (reduceMotion()) return;
  el.gateCard.classList.remove("shake");
  void el.gateCard.offsetWidth;
  el.gateCard.classList.add("shake");
}

function formatBytes(bytes) {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(new Error("读取文件失败"));
    reader.readAsDataURL(file);
  });
}

/* ------------------------------------------------------------------ gate */

el.gateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = el.gatePassword.value.trim();
  if (!password) {
    setStatus(el.gateStatus, "请输入密码");
    return;
  }
  setButton(el.gateSubmit, true, "验证中…");
  setStatus(el.gateStatus, "");
  let disabledForGood = false;
  try {
    await api("/api/v1/mysekai/auth", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    el.gatePassword.value = "";
    showWorkspace();
    upgradeIosLink();
  } catch (error) {
    setStatus(el.gateStatus, error.message);
    shakeGate();
    if (error.status === 503) disabledForGood = true;
  } finally {
    if (!disabledForGood) setButton(el.gateSubmit, false, "进入");
  }
});

/* ------------------------------------------------------------------ 本机记忆 */

let suppressSuggestOnFocus = false;

el.qq.addEventListener("focus", () => {
  if (!suppressSuggestOnFocus) openSuggest();
});
el.qq.addEventListener("input", () => {
  // 从历史里选中已记录的 QQ 时，同时带出对应的游戏 ID。
  const entry = readHistory().find((item) => item.qq === digitsOf(el.qq.value));
  if (entry && entry.gameUserId) el.gameId.value = entry.gameUserId;
  el.uploadStatus.classList.remove("is-ok");
  openSuggest();
});
el.qq.addEventListener("blur", () => window.setTimeout(closeSuggest, 120));
document.addEventListener("click", (event) => {
  if (event.target !== el.qq && !el.qqSuggest.contains(event.target)) {
    closeSuggest();
  }
});

el.gameId.addEventListener("input", () => {
  el.uploadStatus.classList.remove("is-ok");
});

/* ------------------------------------------------------------------ file */

function setFile(file) {
  if (!file) return;
  if (file.size > MAX_FILE_BYTES) {
    setFormStatus("文件超过 4 MB，请确认上传的是响应体");
    return;
  }
  state.file = file;
  el.fileName.textContent = file.name || "响应体";
  el.fileSize.textContent = formatBytes(file.size);
  el.fileChip.classList.remove("hidden");
  el.dropTitle.textContent = "已选择文件，可重新选择";
  setFormStatus("");
}

function clearFile() {
  state.file = null;
  el.fileInput.value = "";
  el.fileChip.classList.add("hidden");
  el.dropTitle.textContent = "拖入响应体，或点击选择";
}

el.dropZone.addEventListener("click", () => el.fileInput.click());
el.fileInput.addEventListener("change", () => setFile(el.fileInput.files[0]));
el.fileClear.addEventListener("click", clearFile);

["dragenter", "dragover"].forEach((type) => {
  el.dropZone.addEventListener(type, (event) => {
    event.preventDefault();
    el.dropZone.classList.add("is-over");
  });
});

["dragleave", "drop"].forEach((type) => {
  el.dropZone.addEventListener(type, (event) => {
    event.preventDefault();
    el.dropZone.classList.remove("is-over");
  });
});

el.dropZone.addEventListener("drop", (event) => {
  const file = event.dataTransfer?.files?.[0];
  if (file) setFile(file);
});

/* ------------------------------------------------------------------ submit */

function showLoading() {
  el.resultEmpty.classList.add("hidden");
  el.resultContent.classList.add("hidden");
  el.resultLoading.classList.remove("hidden");
}

function showEmpty() {
  el.resultLoading.classList.add("hidden");
  el.resultContent.classList.add("hidden");
  el.resultEmpty.classList.remove("hidden");
}

const WATCH_LINES = {
  recorded: ["浇水监听：本轮已记录，未发现稀有材料", ""],
  duplicate: ["浇水监听：这一轮之前已经处理过", ""],
  sent: ["浇水监听：本轮有稀有材料，已播报到群里", "is-ok"],
  failed: ["浇水监听：出图或发送失败，请重新上传", "is-error"],
  unknown: ["浇水监听：缺少当前活动进度，无法识别轮次", "is-error"],
};

function renderWatchLine(watch) {
  el.watchLine.classList.add("hidden");
  el.watchLine.classList.remove("is-ok", "is-error");
  if (!watch || watch.status === "skipped") return;
  const [text, tone] = WATCH_LINES[watch.status] || [watch.message || "", ""];
  if (!text) return;
  el.watchLine.textContent = text;
  el.watchLine.classList.remove("hidden");
  if (tone) el.watchLine.classList.add(tone);
}

/* 摘要卡有生日时 916×1188、没有生日时 916×1076；按实际图片比例显示，避免拉伸。 */
function syncFrameRatio(img) {
  const frame = img && img.closest(".image-frame");
  if (!frame) return;
  const apply = () => {
    if (img.naturalWidth && img.naturalHeight) {
      frame.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
    }
  };
  if (img.complete) apply();
  else img.addEventListener("load", apply, { once: true });
}

function renderResult(data) {
  el.resultLoading.classList.add("hidden");
  el.resultEmpty.classList.add("hidden");
  el.resultContent.classList.remove("hidden");
  const profile = data.profile || {};
  const delivery = data.kind === "birthday_delivery";
  const toggle = (node, hidden) => {
    if (node) node.classList.toggle("hidden", hidden);
  };
  toggle(el.resultMapFigure, delivery);
  toggle(el.resultCardFigure, delivery);
  toggle(el.resultMessage, !delivery);
  if (delivery && el.resultMessage) {
    el.resultMessage.textContent = data.message || "";
  }
  el.profileName.textContent = profile.nickname || "—";
  el.profileId.textContent = profile.game_user_id || "";
  if (profile.avatar_data_url) {
    el.profileAvatar.src = profile.avatar_data_url;
    el.profileAvatar.classList.remove("hidden");
    el.profileAvatarFallback.classList.add("hidden");
  } else {
    el.profileAvatar.removeAttribute("src");
    el.profileAvatar.classList.add("hidden");
    el.profileAvatarFallback.textContent = (profile.nickname || "?").slice(0, 1);
    el.profileAvatarFallback.classList.remove("hidden");
  }

  if (!delivery) {
    el.resultMap.src = data.map;
    el.resultCard.src = data.card;
    syncFrameRatio(el.resultCard);
    el.downloadMap.href = data.map;
    el.downloadCard.href = data.card;
  }
  toggle(el.resultRareFigure, !delivery || !data.rare);
  if (delivery && data.rare && el.resultRare) {
    el.resultRare.src = data.rare;
  }
  toggle(el.resultBirthdayFigure, !delivery || !data.birthday);
  if (delivery && data.birthday && el.resultBirthday) {
    el.resultBirthday.src = data.birthday;
  }

  const push = data.push || {};
  el.pushBadge.classList.remove("is-ok", "is-error");
  if (delivery) {
    el.pushBadge.textContent = "仅生日监听";
  } else if (push.status === "sent") {
    el.pushBadge.textContent = "已推送群里";
    el.pushBadge.classList.add("is-ok");
  } else if (push.status === "failed") {
    el.pushBadge.textContent = "推送失败";
    el.pushBadge.classList.add("is-error");
  } else {
    el.pushBadge.textContent = "推送未配置";
  }

  renderWatchLine(data.watch);
  const sites = Array.isArray(data.sites) ? data.sites.length : 0;
  el.resultMeta.textContent = delivery
    ? ""
    : `${data.points} 点位 · ${data.drops} 掉落 · ${sites} 个站点`;
  state.lastResult = data;

  el.result.scrollIntoView({ behavior: "instant", block: "start" });
}

el.uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy) return;

  const qq = el.qq.value.trim();
  const gameId = el.gameId.value.trim();
  if (!QQ_RE.test(qq)) {
    setFormStatus("请输入正确的 QQ 号");
    el.qq.focus();
    return;
  }
  if (!GAME_ID_RE.test(gameId)) {
    setFormStatus("请输入正确的游戏 ID（15–25 位数字）");
    el.gameId.focus();
    return;
  }
  if (!state.file) {
    setFormStatus("请选择响应体文件");
    return;
  }

  state.busy = true;
  setButton(el.uploadSubmit, true, "生成中…");
  setFormStatus("");
  showLoading();
  try {
    const fileBase64 = await fileToBase64(state.file);
    const data = await api("/api/v1/mysekai/submit", {
      method: "POST",
      body: JSON.stringify({
        qq,
        game_user_id: gameId,
        region: "cn",
        file_base64: fileBase64,
      }),
    });
    renderResult(data);
    rememberEntry(qq, gameId);
  } catch (error) {
    showEmpty();
    setFormStatus(error.message);
    if (error.status === 401) {
      showGate("登录已过期，请重新输入密码");
    }
  } finally {
    state.busy = false;
    setButton(el.uploadSubmit, false, "生成MySekai解析");
  }
});

/* --------------------------------------------------------------- iOS 模块 */

async function upgradeIosLink() {
  const link = document.getElementById("ios-install-link");
  if (!link) return;
  try {
    const data = await api("/api/v1/mysekai/module-link");
    if (!data?.install_url) return;
    link.href = data.install_url;
    link.removeAttribute("download");
    link.textContent = "一键安装到 Shadowrocket";
  } catch {
    // 拿不到就用普通下载链接，不打扰用户
  }
}

/* ------------------------------------------------------------------ boot */

async function initialize() {
  migrateLegacyCookies();
  renderHistory();
  restoreInputs();
  try {
    const data = await api("/api/v1/mysekai/session");
    if (!data.enabled) {
      showGate("入口还没有开启（未配置访问密码）");
      el.gateSubmit.disabled = true;
      return;
    }
    if (data.authenticated) {
      showWorkspace();
      upgradeIosLink();
    } else {
      showGate();
    }
  } catch {
    showGate("服务暂时不可用，请稍后再试");
  }
}

initialize();
