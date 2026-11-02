/* 烤森浇水看板：SSE 实时流，断线降级为 3 秒轮询。 */

const params = new URLSearchParams(location.search);
const QQ = params.get("qq") || "";
const TOKEN = params.get("t") || "";

const feedEl = document.getElementById("feed");
const connEl = document.getElementById("conn");
const statusEl = document.getElementById("status");
const rolesEl = document.getElementById("roles");

let lastId = 0;
let source = null;
let pollTimer = null;
let scrollToBottom = true;

function setConn(state, text) {
  connEl.className = "conn " + state;
  connEl.textContent = text;
}

function pad(value) {
  return String(value).padStart(2, "0");
}

function formatWhen(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function roleLabel(gameUserId) {
  for (const role of rolesEl.querySelectorAll("[data-role-id]")) {
    if (role.dataset.roleId === gameUserId) {
      return role.dataset.roleName || role.dataset.roleLabel;
    }
  }
  return "";
}

function renderEvent(event) {
  const card = document.createElement("div");
  card.className = "card" + (event.kind === "rare" ? " rare" : "");

  const when = document.createElement("div");
  when.className = "when";
  const label = roleLabel(event.game_user_id);
  when.textContent = [formatWhen(event.created_at), label].filter(Boolean).join(" · ");
  card.appendChild(when);

  const headline = document.createElement("div");
  headline.className = "headline";
  if (event.kind === "rare") {
    headline.textContent = `本轮生日浇水稀有掉落（已浇 ${event.rounds} 轮）`;
  } else {
    headline.textContent = `本轮生日浇水无稀有掉落（已浇 ${event.rounds} 轮）`;
  }
  card.appendChild(headline);

  if (event.kind === "rare" && Array.isArray(event.targets) && event.targets.length) {
    const list = document.createElement("ul");
    list.className = "targets";
    for (const target of event.targets) {
      const item = document.createElement("li");
      const name = target.name || "材料";
      const quantity = target.quantity ?? 0;
      const site = target.site_id ? `（地图 ${target.site_id}）` : "";
      item.textContent = `${name} ×${quantity}${site}`;
      list.appendChild(item);
    }
    card.appendChild(list);
  }

  if (event.kind === "rare" && event.image_url) {
    const image = document.createElement("img");
    image.className = "map";
    image.loading = "lazy";
    image.alt = "稀有掉落地图";
    image.src = `${event.image_url}?t=${encodeURIComponent(TOKEN)}`;
    card.appendChild(image);
  }

  feedEl.appendChild(card);
  if (scrollToBottom) {
    window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
  }
}

function renderRoles(roles) {
  rolesEl.replaceChildren();
  for (const role of roles || []) {
    const chip = document.createElement("span");
    chip.className = "role";
    chip.dataset.roleId = role.game_user_id;
    chip.dataset.roleLabel = role.label;
    chip.dataset.roleName = role.nickname || role.label;
    const name = role.nickname || role.label;
    const rare = Number(role.matched_rounds || 0);
    chip.innerHTML = `<b>${escapeHtml(name)}</b> ${Number(role.rounds || 0)} 轮${
      rare ? ` · ${rare} 稀有` : ""
    }`;
    rolesEl.appendChild(chip);
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch])
  );
}

async function loadInitial() {
  const response = await fetch(
    `/api/v1/mysekai/watch/feed?qq=${encodeURIComponent(QQ)}&t=${encodeURIComponent(TOKEN)}&after=0`
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    statusEl.textContent = payload?.error?.message || "链接无效，请重新获取";
    setConn("down", "未连接");
    return false;
  }
  statusEl.textContent = payload.enabled
    ? "浇水监听开启中"
    : "浇水监听当前已关闭（开启后才会记录轮次）";
  renderRoles(payload.roles);
  for (const event of payload.events || []) {
    lastId = Math.max(lastId, event.id);
    renderEvent(event);
  }
  if (!(payload.events || []).length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent =
      "还没有记录到浇水轮次。进游戏浇一次水，这里会实时出现；"
      + "如果浇过水仍没有，检查抓包 App 是否被系统冻结（保持小窗前台、关闭电池优化）";
    feedEl.appendChild(empty);
  }
  return true;
}

function clearEmptyHint() {
  const hint = feedEl.querySelector(".empty");
  if (hint) hint.remove();
}

function openStream() {
  if (source) source.close();
  source = new EventSource(
    `/api/v1/mysekai/watch/stream?qq=${encodeURIComponent(QQ)}&t=${encodeURIComponent(TOKEN)}&after=${lastId}`
  );
  source.onopen = () => {
    setConn("live", "实时连接中");
    stopPolling();
  };
  source.onmessage = (message) => {
    let event;
    try {
      event = JSON.parse(message.data);
    } catch {
      return;
    }
    if (!event || !event.id || event.id <= lastId) return;
    lastId = event.id;
    clearEmptyHint();
    renderEvent(event);
  };
  source.onerror = () => {
    setConn("down", "实时连接断开，改为每 3 秒刷新");
    startPolling();
  };
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    try {
      const response = await fetch(
        `/api/v1/mysekai/watch/feed?qq=${encodeURIComponent(QQ)}&t=${encodeURIComponent(TOKEN)}&after=${lastId}`
      );
      if (!response.ok) return;
      const payload = await response.json();
      for (const event of payload.events || []) {
        lastId = Math.max(lastId, event.id);
        clearEmptyHint();
        renderEvent(event);
      }
      setConn("live", "轮询中（每 3 秒）");
    } catch {
      setConn("down", "网络异常，稍后重试");
    }
  }, 3000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

window.addEventListener("scroll", () => {
  const nearBottom =
    window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
  scrollToBottom = nearBottom;
});

async function main() {
  if (!QQ || !TOKEN) {
    statusEl.textContent = "链接缺少参数，请在群里发送「浇水看板」获取专属链接。";
    setConn("down", "未连接");
    return;
  }
  const ok = await loadInitial();
  if (!ok) return;
  openStream();
}

main();
