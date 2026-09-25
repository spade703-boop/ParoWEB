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
    akito: "#e96629",
    toya: "#2879ad",
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
    const source = String(value || "");
    const lines = [];
    let line = "";
    for (const character of source) {
      const next = line + character;
      if (line && context.measureText(next).width > maxWidth) {
        lines.push(line);
        line = character;
        if (lines.length === maxLines - 1) break;
      } else {
        line = next;
      }
    }
    const consumed = lines.join("").length + line.length;
    if (line) lines.push(consumed < source.length ? truncate(context, source.slice(consumed - line.length), maxWidth) : line);
    return lines.slice(0, maxLines);
  }

  function itemHeight(item) {
    if (item.special_type) return 250;
    return item.is_cooking || item.counts_as_cooking ? 348 : 260;
  }

  function canvasHeight(payload) {
    let height = 218;
    let previousDay = null;
    payload.batches.forEach((batch) => {
      const key = dayKey(batch.createdAt);
      if (payload.kind === "history" && key !== previousDay) {
        height += 56;
        previousDay = key;
      }
      height += 66;
      batch.items.forEach((item) => {
        height += itemHeight(item) + 18;
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
    context.font = `700 48px ${FONT}`;
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

    context.fillStyle = COLORS.faint;
    context.font = `700 24px ${FONT}`;
    context.textAlign = "left";
    context.textBaseline = "alphabetic";
    context.fillText(`#${item.position || 1}`, MARGIN + 34, y + 50);

    const avatarSize = 140;
    const avatarY = y + 34;
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
    context.fillText(truncate(context, item.akito_name, 220), akitoX + avatarSize / 2, y + 207);
    context.fillStyle = COLORS.toya;
    context.fillText(truncate(context, item.toya_name, 220), toyaX + avatarSize / 2, y + 207);

    context.font = `400 22px ${FONT}`;
    context.fillStyle = COLORS.faint;
    context.fillText("彰人", akitoX + avatarSize / 2, y + 239);
    context.fillText("冬弥", toyaX + avatarSize / 2, y + 239);

    if (cooking) {
      drawBadge(context, MARGIN + CONTENT_WIDTH - 18, y + 18, "★ 做饭", COLORS.cookChip, COLORS.cookText);
      fillRounded(context, MARGIN + 30, y + 270, CONTENT_WIDTH - 60, 58, 16, COLORS.cookBg);
      context.font = `500 25px ${FONT}`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillStyle = COLORS.cookInk;
      context.fillText(`快来做 ${item.akito_name} × ${item.toya_name} 的饭吧！`, WIDTH / 2, y + 299);
    }
  }

  function drawSpecialCard(context, item, assets, y) {
    const height = itemHeight(item);
    fillRounded(context, MARGIN, y, CONTENT_WIDTH, height, 28, COLORS.specialBg, COLORS.specialLine, 2);
    context.fillStyle = COLORS.faint;
    context.font = `700 24px ${FONT}`;
    context.textAlign = "left";
    context.textBaseline = "alphabetic";
    context.fillText(`#${item.position || 1}`, MARGIN + 34, y + 50);

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
    context.fillStyle = COLORS.specialInk;
    context.font = `700 34px ${FONT}`;
    context.fillText(truncate(context, item.special_label || "特殊结果", 350), MARGIN + 530, y + 108);
    context.fillStyle = COLORS.muted;
    context.font = `400 25px ${FONT}`;
    wrapLines(context, item.special_message || "", 340, 2).forEach((line, index) => {
      context.fillText(line, MARGIN + 530, y + 151 + index * 36);
    });
    if (item.counts_as_cooking) {
      drawBadge(context, MARGIN + CONTENT_WIDTH - 18, y + 18, "★ 计入做饭", COLORS.cookChip, COLORS.cookText);
    }
  }

  function drawHeader(context, payload) {
    context.fillStyle = COLORS.akito;
    context.beginPath();
    context.arc(MARGIN + 30, 62, 28, 0, Math.PI * 2);
    context.fill();
    context.globalAlpha = 0.82;
    context.fillStyle = COLORS.toya;
    context.beginPath();
    context.arc(MARGIN + 53, 62, 28, 0, Math.PI * 2);
    context.fill();
    context.globalAlpha = 1;

    context.fillStyle = COLORS.ink;
    context.font = `700 30px ${FONT}`;
    context.textAlign = "left";
    context.textBaseline = "alphabetic";
    context.fillText("抽派生 · PARO DRAW", MARGIN + 98, 72);
    context.font = `700 54px ${FONT}`;
    context.fillText(payload.title, MARGIN, 148);
    context.fillStyle = COLORS.muted;
    context.font = `400 25px ${FONT}`;
    context.fillText(payload.subtitle, MARGIN, 190);
  }

  function drawBatchHeader(context, batch, y) {
    const selected = batch.items.length;
    const requested = Number(batch.requestedCount) || selected;
    const countText = selected === requested ? `本次抽取 · ${requested} 抽` : `本次抽取 · 已选 ${selected}/${requested}`;
    context.fillStyle = COLORS.ink;
    context.font = `700 27px ${FONT}`;
    context.textAlign = "left";
    context.textBaseline = "alphabetic";
    context.fillText(countText, MARGIN, y + 34);
    context.fillStyle = COLORS.faint;
    context.font = `400 22px ${FONT}`;
    context.textAlign = "right";
    context.fillText(`${formatDateTime(batch.createdAt)} · ${modeText(batch)}`, WIDTH - MARGIN, y + 34);
    context.strokeStyle = COLORS.lineSoft;
    context.lineWidth = 2;
    context.beginPath();
    context.moveTo(MARGIN, y + 58);
    context.lineTo(WIDTH - MARGIN, y + 58);
    context.stroke();
  }

  async function render(payload) {
    const totalItems = payload.batches.reduce((sum, batch) => sum + batch.items.length, 0);
    if (!totalItems) throw new Error("没有可导出的记录");
    if (totalItems > MAX_ITEMS) throw new Error(`一张图片最多导出 ${MAX_ITEMS} 条记录`);
    if (document.fonts?.ready) await document.fonts.ready;
    const assets = await loadAssets(payload);
    const canvas = document.createElement("canvas");
    canvas.width = WIDTH;
    canvas.height = canvasHeight(payload);
    const context = canvas.getContext("2d");
    if (!context) throw new Error("当前浏览器不支持图片生成");

    context.fillStyle = COLORS.paper;
    context.fillRect(0, 0, canvas.width, canvas.height);
    drawHeader(context, payload);

    let y = 218;
    let previousDay = null;
    payload.batches.forEach((batch) => {
      const key = dayKey(batch.createdAt);
      if (payload.kind === "history" && key !== previousDay) {
        context.fillStyle = COLORS.muted;
        context.font = `700 25px ${FONT}`;
        context.textAlign = "left";
        context.textBaseline = "alphabetic";
        context.fillText(formatDay(batch.createdAt), MARGIN, y + 34);
        y += 56;
        previousDay = key;
      }
      drawBatchHeader(context, batch, y);
      y += 66;
      batch.items.forEach((item) => {
        if (item.special_type) drawSpecialCard(context, item, assets, y);
        else drawPairCard(context, item, assets, y);
        y += itemHeight(item) + 18;
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

    const blob = await new Promise((resolve, reject) => {
      canvas.toBlob((value) => {
        if (value) resolve(value);
        else reject(new Error("图片编码失败，请重试"));
      }, "image/png");
    });
    const stamp = formatDateTime(payload.generatedAt).replace(/[-: ]/g, "");
    const filename = `${payload.kind === "history" ? "paro-history" : "paro-current"}-${stamp}.png`;
    return { canvas, blob, filename };
  }

  window.ParoImageExporter = Object.freeze({ MAX_ITEMS, render });
})();
