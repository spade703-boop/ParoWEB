(() => {
  "use strict";

  const WIDTH = 1080;
  const MARGIN = 72;
  const CONTENT_WIDTH = WIDTH - MARGIN * 2;
  const MAX_ITEMS = 12;
  const FONT = '"Microsoft YaHei", "Noto Sans SC", "PingFang SC", sans-serif';
  const COLORS = {
    paper: "#f7f1e9",
    card: "#fffdf9",
    cardDeep: "#faf5ee",
    ink: "#292826",
    muted: "#716e68",
    faint: "#a89684",
    line: "#dec9c4",
    lineSoft: "#e3d8c9",
    akito: "#ff7722",
    toya: "#0077dd",
    cookBg: "#fff0bf",
    cookInk: "#855d00",
    cookLine: "#dfa93a",
    cookChip: "#855d00",
    cookText: "#fff6dc",
    specialBg: "#f9f7fc",
    specialLine: "#ddd4e7",
    specialInk: "#5c4c8c",
  };

  function roundedPath(context, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    context.beginPath();
    context.moveTo(x + r, y);
    context.arcTo(x + width, y, x + width, y + height, r);
    context.arcTo(x + width, y + height, x, y + height, r);
    context.arcTo(x, y + height, x, y, r);
    context.arcTo(x, y, x + width, y, r);
    context.closePath();
  }

  function fillRounded(context, x, y, width, height, radius, fill, stroke = null, lineWidth = 1) {
    roundedPath(context, x, y, width, height, radius);
    context.fillStyle = fill;
    context.fill();
    if (stroke) {
      context.strokeStyle = stroke;
      context.lineWidth = lineWidth;
      context.stroke();
    }
  }

  function localDate(iso) {
    return iso ? new Date(iso) : new Date();
  }

  function pad(value) {
    return String(value).padStart(2, "0");
  }

  function dayKey(iso) {
    const date = localDate(iso);
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  function formatDateTime(iso) {
    const date = localDate(iso);
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function formatDay(iso) {
    const date = localDate(iso);
    return `${date.getFullYear()} 年 ${date.getMonth() + 1} 月 ${date.getDate()} 日`;
  }

  function formatTime(iso) {
    const date = localDate(iso);
    return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function modeText(batch) {
    const labels = { none: "双方随机", akito: "固定彰人", toya: "固定冬弥" };
    const base = labels[batch.fixedSide] || labels.none;
    return batch.fixedSide !== "none" && batch.fixedName ? `${base} · ${batch.fixedName}` : base;
  }

  function truncate(context, value, maxWidth) {
    const text = String(value || "");
    if (context.measureText(text).width <= maxWidth) return text;
    let output = text;
    while (output.length && context.measureText(`${output}…`).width > maxWidth) {
      output = output.slice(0, -1);
    }
    return `${output}…`;
  }

  function wrapLines(context, value, maxWidth, maxLines = 2) {
    // 配置里的文案可能带硬换行（一行说盘子里有什么，一行揣度是谁的）。
    // 先按段落拆开，再逐段按宽度折行，行数预算由各段共享。
    const paragraphs = String(value || "")
      .split("\n")
      .map((part) => part.trim())
      .filter(Boolean);
    const lines = [];
    for (const paragraph of paragraphs) {
      let line = "";
      for (const character of paragraph) {
        const next = line + character;
        if (line && context.measureText(next).width > maxWidth) {
          lines.push(line);
          line = character;
        } else {
          line = next;
        }
      }
      if (line) lines.push(line);
      if (lines.length > maxLines) break;
    }
    if (lines.length <= maxLines) return lines;
    // 超出行数预算：把多出来的内容折进最后一行，末尾补省略号
    let rest = lines.slice(maxLines - 1).join("");
    while (rest.length && context.measureText(`${rest}…`).width > maxWidth) {
      rest = rest.slice(0, -1);
    }
    return [...lines.slice(0, maxLines - 1), `${rest}…`];
  }

  function itemHeight(item) {
    if (item.special_type) return 250;
    return item.is_cooking || item.counts_as_cooking ? 348 : 260;
  }

  function exportGroups(payload) {
    const batches = payload.batches.filter((batch) => batch.items.length);
    if (payload.kind !== "history") return batches.map((batch) => ({ batches: [batch] }));
    const days = new Map();
    batches.forEach((batch) => {
      const key = dayKey(batch.createdAt);
      if (!days.has(key)) days.set(key, { createdAt: batch.createdAt, batches: [] });
      days.get(key).batches.push(batch);
    });
    return Array.from(days.values());
  }

  function canvasHeight(groups) {
    let height = 180;
    groups.forEach((group) => {
      group.batches.forEach((batch) => {
        height += 56;
        batch.items.forEach((item) => {
          height += itemHeight(item) + 18;
        });
      });
      height += 14;
    });
    return height + 94;
  }

  function loadImage(url) {
    if (!url) return Promise.resolve(null);
    return new Promise((resolve) => {
      const image = new Image();
      image.decoding = "async";
      image.onload = () => resolve(image);
      image.onerror = () => resolve(null);
      image.src = new URL(url, window.location.href).href;
    });
  }

  async function loadAssets(payload) {
    const urls = new Set();
    payload.batches.forEach((batch) => {
      batch.items.forEach((item) => {
        if (item.akito_avatar_url) urls.add(item.akito_avatar_url);
        if (item.toya_avatar_url) urls.add(item.toya_avatar_url);
        (item.special_asset_urls || []).forEach((url) => urls.add(url));
      });
    });
    const pairs = await Promise.all(Array.from(urls, async (url) => [url, await loadImage(url)]));
    return new Map(pairs);
  }

  function drawAvatar(context, image, fallback, x, y, size, tint) {
    fillRounded(context, x, y, size, size, 22, COLORS.cardDeep, COLORS.lineSoft, 2);
    if (image) {
      context.save();
      roundedPath(context, x, y, size, size, 22);
      context.clip();
      const scale = Math.max(size / image.naturalWidth, size / image.naturalHeight);
      const width = image.naturalWidth * scale;
      const height = image.naturalHeight * scale;
      context.drawImage(image, x + (size - width) / 2, y + (size - height) / 2, width, height);
      context.restore();
      return;
    }
    context.fillStyle = tint;
    context.font = `700 ${Math.round(size * .34)}px ${FONT}`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(String(fallback || "?").slice(0, 1), x + size / 2, y + size / 2);
  }

  function drawBadge(context, x, y, text, background, foreground) {
    context.font = `700 24px ${FONT}`;
    const width = Math.max(116, context.measureText(text).width + 54);
    fillRounded(context, x - width, y, width, 46, 23, background);
    context.fillStyle = foreground;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(text, x - width / 2, y + 23);
  }

  function drawPairCard(context, item, assets, y) {
    const cooking = Boolean(item.is_cooking || item.counts_as_cooking);
    const height = itemHeight(item);
    fillRounded(
      context,
      MARGIN,
      y,
      CONTENT_WIDTH,
      height,
      28,
      COLORS.card,
      cooking ? COLORS.cookLine : COLORS.line,
      2,
    );

    const avatarSize = 140;
    const contentY = y - 8;
    const avatarY = contentY + 34;
    const akitoX = MARGIN + 238;
    const toyaX = MARGIN + 558;
    drawAvatar(context, assets.get(item.akito_avatar_url), item.akito_name, akitoX, avatarY, avatarSize, COLORS.akito);
    drawAvatar(context, assets.get(item.toya_avatar_url), item.toya_name, toyaX, avatarY, avatarSize, COLORS.toya);

    context.fillStyle = COLORS.faint;
    context.font = `400 42px ${FONT}`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText("×", WIDTH / 2, avatarY + avatarSize / 2);

    context.font = `700 30px ${FONT}`;
    context.fillStyle = COLORS.akito;
    context.fillText(truncate(context, item.akito_name, 220), akitoX + avatarSize / 2, contentY + 207);
    context.fillStyle = COLORS.toya;
    context.fillText(truncate(context, item.toya_name, 220), toyaX + avatarSize / 2, contentY + 207);

    context.font = `400 22px ${FONT}`;
    context.fillStyle = COLORS.faint;
    context.fillText("彰人", akitoX + avatarSize / 2, contentY + 239);
    context.fillText("冬弥", toyaX + avatarSize / 2, contentY + 239);

    if (cooking) {
      drawCookingDetails(context, y, `快来做 ${item.akito_name} × ${item.toya_name} 的饭吧！`);
    }
  }

  function drawCookingDetails(context, y, message) {
    drawBadge(context, MARGIN + CONTENT_WIDTH - 18, y + 18, "★ 做饭", COLORS.cookChip, COLORS.cookText);
    fillRounded(context, MARGIN + 30, y + 270, CONTENT_WIDTH - 60, 58, 16, COLORS.cookBg);
    context.font = `500 25px ${FONT}`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillStyle = COLORS.cookInk;
    context.fillText(message, WIDTH / 2, y + 299);
  }

  // 特殊卡里「标题 + 下面几行文案」当一个整体，跟左边图案的纵向中心对齐。
  // 图案贴 y + 68、边长 112 → 中心 y + 124（刚好也是 250 高卡片的中心）。
  // 中文字形的墨迹范围实测：上沿 ≈ 基线 - 0.85×字号，下沿 ≈ 基线 + 0.12×字号，
  // 所以文案有几行，标题的落点就得跟着变，不能写死一个偏移。
  const SPECIAL_ART_CENTER = 68 + 112 / 2;
  const SPECIAL_TITLE_BASE = 108;
  const SPECIAL_BODY_BASE = 151;
  const SPECIAL_BODY_STEP = 36;

  function specialTextOffsetY(lineCount) {
    const blockTop = SPECIAL_TITLE_BASE - Math.round(34 * 0.85);
    const blockBottom = SPECIAL_BODY_BASE + (lineCount - 1) * SPECIAL_BODY_STEP + Math.round(25 * 0.12);
    return SPECIAL_ART_CENTER - (blockTop + blockBottom) / 2;
  }

  function drawSpecialCard(context, item, assets, y) {
    const height = itemHeight(item);
    const foxbun = item.special_type === "foxbun";
    const textX = MARGIN + 450;
    const textWidth = WIDTH - MARGIN - 34 - textX;
    fillRounded(context, MARGIN, y, CONTENT_WIDTH, height, 28, COLORS.specialBg, COLORS.specialLine, 2);
    const urls = item.special_asset_urls || [];
    const size = 112;
    const gap = 14;
    const count = Math.max(1, Math.min(3, urls.length));
    const groupWidth = count * size + (count - 1) * gap;
    const startX = MARGIN + 300 - groupWidth / 2;
    for (let index = 0; index < count; index += 1) {
      const url = urls[index];
      drawAvatar(context, assets.get(url), item.special_label, startX + index * (size + gap), y + 68, size, COLORS.specialInk);
    }

    context.textAlign = "left";
    context.textBaseline = "alphabetic";
    context.font = `700 34px ${FONT}`;
    const title = foxbun ? "发现了一对正在贴贴的狐兔" : truncate(context, item.special_label || "特殊结果", textWidth);
    context.font = `400 25px ${FONT}`;
    const bodyLines = wrapLines(context, foxbun ? "快来做狐兔饭吧！" : item.special_message || "", textWidth, 2);
    const textOffsetY = specialTextOffsetY(bodyLines.length);

    context.fillStyle = COLORS.specialInk;
    context.font = `700 34px ${FONT}`;
    context.fillText(title, textX, y + SPECIAL_TITLE_BASE + textOffsetY);

    context.fillStyle = COLORS.muted;
    context.font = `400 25px ${FONT}`;
    bodyLines.forEach((line, index) => {
      context.fillText(line, textX, y + SPECIAL_BODY_BASE + textOffsetY + index * SPECIAL_BODY_STEP);
    });
    if (item.counts_as_cooking) {
      drawBadge(context, MARGIN + CONTENT_WIDTH - 18, y + 18, foxbun ? "★ 做饭" : "★ 计入做饭", COLORS.cookChip, COLORS.cookText);
    }
  }

  function drawHeader(context, payload) {
    context.fillStyle = COLORS.akito;
    context.beginPath();
    context.arc(MARGIN + 30, 62, 28, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = COLORS.toya;
    context.beginPath();
    context.arc(MARGIN + 53, 62, 28, 0, Math.PI * 2);
    context.fill();

    context.fillStyle = COLORS.ink;
    context.font = `700 30px ${FONT}`;
    context.textAlign = "left";
    context.textBaseline = "alphabetic";
    context.fillText("抽派生 · PARO DRAW", MARGIN + 98, 72);
    context.font = `700 54px ${FONT}`;
    context.fillText(payload.title, MARGIN, payload.kind === "history" ? 161 : 148);
  }

  function drawBatchDetails(context, batch, y) {
    context.textBaseline = "alphabetic";
    context.fillStyle = COLORS.faint;
    context.font = `400 22px ${FONT}`;
    context.textAlign = "right";
    context.fillText(`${formatTime(batch.createdAt)} · ${modeText(batch)}`, WIDTH - MARGIN, y + 34);
  }

  async function renderProfile(payload) {
    const canvas = await window.ParoProfileExporter.renderCanvas(payload.profile);
    return encodeCanvas(canvas, payload);
  }

  async function render(payload) {
    if (payload.kind === "profile") return renderProfile(payload);
    const totalItems = payload.batches.reduce((sum, batch) => sum + batch.items.length, 0);
    if (!totalItems) throw new Error("没有可导出的记录");
    if (totalItems > MAX_ITEMS) throw new Error(`一张图片最多导出 ${MAX_ITEMS} 条记录`);
    if (document.fonts?.ready) await document.fonts.ready;
    const assets = await loadAssets(payload);
    const groups = exportGroups(payload);
    const canvas = document.createElement("canvas");
    canvas.width = WIDTH;
    canvas.height = canvasHeight(groups);
    const context = canvas.getContext("2d");
    if (!context) throw new Error("当前浏览器不支持图片生成");

    context.fillStyle = COLORS.paper;
    context.fillRect(0, 0, canvas.width, canvas.height);
    drawHeader(context, payload);

    let y = 180;
    groups.forEach((group) => {
      if (payload.kind === "history") {
        context.fillStyle = COLORS.muted;
        context.font = `700 25px ${FONT}`;
        context.textAlign = "left";
        context.textBaseline = "alphabetic";
        context.fillText(formatDay(group.createdAt), MARGIN, y + 34);
      }
      group.batches.forEach((batch) => {
        drawBatchDetails(context, batch, y);
        y += 56;
        batch.items.forEach((item) => {
          if (item.special_type) drawSpecialCard(context, item, assets, y);
          else drawPairCard(context, item, assets, y);
          y += itemHeight(item) + 18;
        });
      });
      y += 14;
    });

    context.fillStyle = COLORS.faint;
    context.font = `400 22px ${FONT}`;
    context.textAlign = "left";
    context.textBaseline = "middle";
    context.fillText("akitoya.top", MARGIN, canvas.height - 52);
    context.textAlign = "right";
    context.fillText(`生成于 ${formatDateTime(payload.generatedAt)}`, WIDTH - MARGIN, canvas.height - 52);

    return encodeCanvas(canvas, payload);
  }

  async function encodeCanvas(canvas, payload) {
    const blob = await new Promise((resolve, reject) => {
      canvas.toBlob((value) => {
        if (value) resolve(value);
        else reject(new Error("图片编码失败，请重试"));
      }, "image/png");
    });
    const stamp = formatDateTime(payload.generatedAt).replace(/[-: ]/g, "");
    const prefix = { history: "paro-history", profile: "paro-profile", current: "paro-current" }[payload.kind] || "paro-current";
    const filename = `${prefix}-${stamp}.png`;
    return { canvas, blob, filename };
  }

  window.ParoImageExporter = Object.freeze({ MAX_ITEMS, render });
})();
