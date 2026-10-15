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

  function reportText(context, text, left, top, size, color = COLORS.ink, align = "left") {
    context.font = `700 ${size}px ${FONT}`;
    context.fillStyle = color;
    context.textAlign = align;
    context.textBaseline = "middle";
    context.fillText(text, left, top);
  }

  function reportSection(context, title, top, color = COLORS.akito) {
    fillRounded(context, MARGIN, top + 2, 6, 28, 3, color);
    reportText(context, title, MARGIN + 20, top + 16, 28);
  }

  function reportRankHeight(items) {
    return 82 + Math.max(1, items.length) * 80;
  }

  function drawReportRanks(context, items, assets, title, color, top) {
    const height = reportRankHeight(items);
    fillRounded(context, MARGIN, top, CONTENT_WIDTH, height, 24, COLORS.card, `${color}55`, 2);
    fillRounded(context, MARGIN + 28, top + 28, 6, 28, 3, color);
    reportText(context, title, MARGIN + 48, top + 42, 28, color);
    if (!items.length) {
      reportText(context, "还没有抽取记录", WIDTH / 2, top + 112, 24, COLORS.muted, "center");
    }
    items.forEach((item, index) => {
      const center = top + 108 + index * 80;
      fillRounded(context, MARGIN + 28, center - 21, 92, 42, 10, index === 0 ? color : `${color}18`);
      reportText(context, `TOP ${index + 1}`, MARGIN + 74, center, 20, index === 0 ? COLORS.card : color, "center");
      context.font = `700 26px ${FONT}`;
      reportText(context, truncate(context, item.name, CONTENT_WIDTH - 370), MARGIN + 142, center, 26, color);
      drawAvatar(context, assets.get(item.avatar_url), item.name, WIDTH - MARGIN - 190, center - 28, 56, color);
      reportText(context, `${item.count} 次`, WIDTH - MARGIN - 28, center, 24, color, "right");
    });
    return top + height;
  }

  function drawReportDishes(context, items, assets, top) {
    reportSection(context, "抽到的派生饭", top);
    const start = top + 54;
    const width = (CONTENT_WIDTH - 16) / 2;
    if (!items.length) {
      fillRounded(context, MARGIN, start, CONTENT_WIDTH, 80, 20, COLORS.card, COLORS.lineSoft);
      reportText(context, "还没有抽到做饭彩蛋", WIDTH / 2, start + 40, 24, COLORS.muted, "center");
      return start + 80;
    }
    items.forEach((item, index) => {
      const left = MARGIN + (index % 2) * (width + 16);
      const rowTop = start + Math.floor(index / 2) * 86;
      fillRounded(context, left, rowTop, width, 70, 35, COLORS.card, COLORS.lineSoft);
      drawAvatar(context, assets.get(item.akito_avatar_url), item.akito_name, left + 14, rowTop + 15, 40, COLORS.akito);
      drawAvatar(context, assets.get(item.toya_avatar_url), item.toya_name, left + 44, rowTop + 15, 40, COLORS.toya);
      context.font = `700 21px ${FONT}`;
      const countLabel = `×${item.count}`;
      const countWidth = context.measureText(countLabel).width;
      const nameWidth = (width - 146 - countWidth) / 2;
      const akitoName = truncate(context, item.akito_name, nameWidth);
      const toyaName = truncate(context, item.toya_name, nameWidth);
      const nameLeft = left + 100;
      reportText(context, akitoName, nameLeft, rowTop + 35, 21, COLORS.akito);
      const crossLeft = nameLeft + context.measureText(akitoName).width + 4;
      reportText(context, "×", crossLeft, rowTop + 35, 21, COLORS.faint);
      reportText(context, toyaName, crossLeft + 24, rowTop + 35, 21, COLORS.toya);
      reportText(context, countLabel, left + width - 18, rowTop + 35, 21, COLORS.muted, "right");
    });
    return start + Math.ceil(items.length / 2) * 86 - 16;
  }

  function drawReportSpecials(context, items, assets, top) {
    reportSection(context, "特殊结果", top, COLORS.toya);
    const start = top + 54;
    const width = (CONTENT_WIDTH - 16) / 2;
    if (!items.length) {
      fillRounded(context, MARGIN, start, CONTENT_WIDTH, 80, 20, COLORS.card, COLORS.lineSoft);
      reportText(context, "还没有特殊结果", WIDTH / 2, start + 40, 24, COLORS.muted, "center");
      return start + 80;
    }
    items.forEach((item, index) => {
      const left = MARGIN + (index % 2) * (width + 16);
      const rowTop = start + Math.floor(index / 2) * 102;
      fillRounded(context, left, rowTop, width, 86, 20, COLORS.card, COLORS.lineSoft);
      const icons = item.asset_urls?.length ? item.asset_urls.slice(0, 2) : [null];
      icons.forEach((url, iconIndex) => {
        drawAvatar(context, assets.get(url), item.label, left + 18 + iconIndex * 32, rowTop + 19, 48, iconIndex ? COLORS.toya : COLORS.akito);
      });
      context.font = `700 24px ${FONT}`;
      reportText(context, truncate(context, item.label, width - 236), left + 116, rowTop + 43, 24);
      reportText(context, `${item.count} 次`, left + width - 22, rowTop + 43, 24, COLORS.toya, "right");
    });
    return start + Math.ceil(items.length / 2) * 102 - 16;
  }

  async function renderProfile(payload) {
    const profile = payload.profile;
    if (!profile) throw new Error("个人数据尚未加载，请稍后重试。");
    if (document.fonts?.ready) await document.fonts.ready;
    const akitoItems = (profile.akito_top || []).slice(0, 3);
    const toyaItems = (profile.toya_top || []).slice(0, 3);
    const dishes = profile.cooking_pairs || [];
    const specials = profile.special_counts || [];
    const dishHeight = dishes.length ? Math.ceil(dishes.length / 2) * 86 - 16 : 80;
    const specialHeight = specials.length ? Math.ceil(specials.length / 2) * 102 - 16 : 80;
    const height = 340 + reportRankHeight(akitoItems) + 24 + reportRankHeight(toyaItems)
      + 32 + 54 + dishHeight + 32 + 54 + specialHeight + 110;
    if (height > 16000) throw new Error("做饭组合过多，整页图片过长，请使用选择导出。");

    const urls = new Set();
    [...akitoItems, ...toyaItems].forEach((item) => { if (item.avatar_url) urls.add(item.avatar_url); });
    dishes.forEach((item) => {
      if (item.akito_avatar_url) urls.add(item.akito_avatar_url);
      if (item.toya_avatar_url) urls.add(item.toya_avatar_url);
    });
    specials.forEach((item) => (item.asset_urls || []).slice(0, 2).forEach((url) => urls.add(url)));
    const assets = new Map(await Promise.all(Array.from(urls, async (url) => [url, await loadImage(url)])));
    const canvas = document.createElement("canvas");
    canvas.width = WIDTH;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("当前浏览器不支持图片生成");
    context.fillStyle = COLORS.paper;
    context.fillRect(0, 0, WIDTH, height);
    context.fillStyle = COLORS.akito;
    context.fillRect(0, 0, WIDTH / 2, 8);
    context.fillStyle = COLORS.toya;
    context.fillRect(WIDTH / 2, 0, WIDTH / 2, 8);

    let titleLeft = MARGIN;
    [["我的", COLORS.ink], ["彰", COLORS.akito], ["冬", COLORS.toya], ["派生记录", COLORS.ink]].forEach(([text, color]) => {
      reportText(context, text, titleLeft, 88, 48, color);
      titleLeft += context.measureText(text).width;
    });
    reportText(context, "全部抽取统计 · 含固定模式", MARGIN, 142, 22, COLORS.muted);
    const specialCount = specials.reduce((sum, item) => sum + (item.count || 0), 0);
    const stats = [
      ["累计抽取", profile.draw_count || 0, COLORS.ink],
      ["做饭次数", profile.cooking_count || 0, COLORS.akito],
      ["特殊结果", specialCount, COLORS.toya],
      ["常规组合", Math.max(0, (profile.draw_count || 0) - specialCount), COLORS.ink],
    ];
    const statWidth = (CONTENT_WIDTH - 48) / 4;
    stats.forEach(([label, value, color], index) => {
      const left = MARGIN + index * (statWidth + 16);
      fillRounded(context, left, 184, statWidth, 126, 20, COLORS.card, COLORS.lineSoft);
      reportText(context, label, left + 22, 218, 21, COLORS.muted);
      context.font = `700 40px ${FONT}`;
      const size = Math.min(40, 40 * (statWidth - 44) / Math.max(1, context.measureText(String(value)).width));
      reportText(context, String(value), left + 22, 267, size, color);
    });

    let sectionTop = drawReportRanks(context, akitoItems, assets, "抽到最多的彰人派生 TOP 3", COLORS.akito, 340);
    sectionTop = drawReportRanks(context, toyaItems, assets, "抽到最多的冬弥派生 TOP 3", COLORS.toya, sectionTop + 24);
    sectionTop = drawReportDishes(context, dishes, assets, sectionTop + 32);
    sectionTop = drawReportSpecials(context, specials, assets, sectionTop + 32);
    context.fillStyle = COLORS.lineSoft;
    context.fillRect(MARGIN, sectionTop + 34, CONTENT_WIDTH, 1);
    reportText(context, "Generated by 寒星", MARGIN, sectionTop + 70, 20, COLORS.faint);
    reportText(context, formatDateTime(payload.generatedAt), WIDTH - MARGIN, sectionTop + 70, 20, COLORS.faint, "right");
    return encodeCanvas(canvas, payload);
  }

  async function render(payload) {
    if (payload.kind === "profile") return renderProfile(payload);
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
