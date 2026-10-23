/* ==========================================================================
   MySekai 群友入口 · 页面交互
   只做三件事：密码门、记住 QQ/ID、上传 → 展示两张图。
   所有请求都走同源 /api/v1/mysekai/*，不引任何第三方脚本。
   ========================================================================== */

const $ = (selector, root = document) => root.querySelector(selector);
const MAX_FILE_BYTES = 4 * 1024 * 1024;
const QQ_RE = /^\d{5,12}$/;
const GAME_ID_RE = /^\d{15,25}$/;

/* QQ 号与游戏 ID 存在本机 cookie 里，方便下次自动填回；清 cookie 即失效。 */
const COOKIE_QQ = "paro_mysekai_qq";
const COOKIE_GAME_ID = "paro_mysekai_gid";
const COOKIE_MAX_AGE = 60 * 60 * 24 * 180;

function readCookie(name) {
  const prefix = `${name}=`;
  for (const part of document.cookie.split(";")) {
    const item = part.trim();
    if (item.startsWith(prefix)) return item.slice(prefix.length);
  }
  return "";
}

function saveInput(name, value) {
  const digits = String(value || "").replace(/\D/g, "");
  const secure = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = digits
    ? `${name}=${digits}; Path=/; Max-Age=${COOKIE_MAX_AGE}; SameSite=Lax${secure}`
    : `${name}=; Path=/; Max-Age=0; SameSite=Lax${secure}`;
}

function restoreInputs() {
  const qq = readCookie(COOKIE_QQ);
  const gameId = readCookie(COOKIE_GAME_ID);
  if (qq) el.qq.value = qq;
  if (gameId) el.gameId.value = gameId;
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
  window.setTimeout(() => el.qq.focus(), 0);
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

el.qq.addEventListener("input", () => {
  saveInput(COOKIE_QQ, el.qq.value.trim());
  el.uploadStatus.classList.remove("is-ok");
});

el.gameId.addEventListener("input", () => {
  saveInput(COOKIE_GAME_ID, el.gameId.value.trim());
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

function renderResult(data) {
  el.resultLoading.classList.add("hidden");
  el.resultEmpty.classList.add("hidden");
  el.resultContent.classList.remove("hidden");

  const profile = data.profile || {};
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

  el.resultMap.src = data.map;
  el.resultCard.src = data.card;
  el.downloadMap.href = data.map;
  el.downloadCard.href = data.card;

  const push = data.push || {};
  el.pushBadge.classList.remove("is-ok", "is-error");
  if (push.status === "sent") {
    el.pushBadge.textContent = "已推送群里";
    el.pushBadge.classList.add("is-ok");
  } else if (push.status === "failed") {
    el.pushBadge.textContent = "推送失败";
    el.pushBadge.classList.add("is-error");
  } else {
    el.pushBadge.textContent = "推送未配置";
  }

  renderWatchLine(data.watch);
  const sites = Array.isArray(data.sites) ? data.sites.length : 0;  el.resultMeta.textContent = `${data.points} 点位 · ${data.drops} 掉落 · ${sites} 个站点`;
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
