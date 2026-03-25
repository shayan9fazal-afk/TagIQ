const $ = (sel) => document.querySelector(sel);

function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("trendtags_theme", theme);
}

function initTheme() {
  const saved = localStorage.getItem("trendtags_theme");
  if (saved === "light" || saved === "dark") {
    setTheme(saved);
  } else {
    const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
    setTheme(prefersLight ? "light" : "dark");
  }
}

function toast(msg) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = msg;
  el.classList.add("is-on");
  window.clearTimeout(toast._t);
  toast._t = window.setTimeout(() => el.classList.remove("is-on"), 1400);
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text || "");
    toast("Copied to clipboard");
  } catch (e) {
    // Fallback for some browsers
    const ta = document.createElement("textarea");
    ta.value = text || "";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    toast("Copied to clipboard");
  }
}

function formatNumber(n) {
  const x = Number(n);
  if (!Number.isFinite(x)) return "";
  return x.toLocaleString();
}

function chipList(el, items, emptyText) {
  el.innerHTML = "";
  if (!items || items.length === 0) {
    const span = document.createElement("span");
    span.className = "chip chip--muted";
    span.textContent = emptyText || "No data";
    el.appendChild(span);
    return;
  }
  for (const it of items) {
    const span = document.createElement("span");
    span.className = "chip";
    span.textContent = it;
    el.appendChild(span);
  }
}

function phraseList(el, items) {
  el.innerHTML = "";
  if (!items || items.length === 0) {
    const li = document.createElement("li");
    li.textContent = "No data";
    el.appendChild(li);
    return;
  }
  for (const it of items.slice(0, 30)) {
    const li = document.createElement("li");
    const phrase = it.phrase || "";
    const score = typeof it.score === "number" ? it.score : it.score ?? "";
    li.innerHTML = `${escapeHtml(phrase)} <span class="small">${escapeHtml(String(score))}</span>`;
    el.appendChild(li);
  }
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function tableVideos(el, items) {
  el.innerHTML = "";
  if (!items || items.length === 0) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="3" class="muted">No data</td>`;
    el.appendChild(tr);
    return;
  }
  for (const v of items.slice(0, 10)) {
    const tr = document.createElement("tr");
    const title = v.title || "";
    const url = v.url || "#";
    const views = formatNumber(v.views);
    const vpd = formatNumber(v.viewsPerDay);
    tr.innerHTML = `
      <td>
        <a href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${escapeHtml(title)}</a>
        <div class="sub">${escapeHtml(v.videoId || "")}</div>
      </td>
      <td class="num">${escapeHtml(views)}</td>
      <td class="num">${escapeHtml(vpd)}</td>
    `;
    el.appendChild(tr);
  }
}

function getApiBase() {
  const v = ($("#api_base")?.value || "").trim();
  if (v) return v.replace(/\/+$/, "");
  // Default: same origin (works if hosted with backend)
  return "";
}

async function analyze(payload) {
  const base = getApiBase();
  const url = `${base}/api/analyze`;
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = data?.error || `Request failed (${r.status})`;
    throw new Error(msg);
  }
  return data;
}

function downloadJson(obj) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `trendtags_${Date.now()}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function setBusy(busy) {
  const btn = $("#analyzeBtn");
  if (!btn) return;
  btn.disabled = busy;
  btn.querySelector(".btn__text").textContent = busy ? "Analyzing..." : "Analyze channel";
}

let outputFormat = "youtube"; // youtube | csv
function setFormat(fmt) {
  outputFormat = fmt;
  document.querySelectorAll(".segmented__btn").forEach((b) => {
    b.classList.toggle("is-active", b.dataset.format === fmt);
  });
}

function buildCopyAll(result) {
  const copy = result.copy || {};
  if (outputFormat === "csv") {
    const tags = (result?.suggested?.tags || []).join(",");
    const hashtags = (result?.suggested?.hashtags || []).join(",");
    const shorts = (result?.suggested?.shorts_hashtags || []).join(",");
    const global = (result?.suggested?.global_hashtags || []).join(",");
    return `tags,hashtags,shorts_hashtags,global_hashtags\n"${tags}","${hashtags}","${shorts}","${global}"\n`;
  }
  return copy.all || "";
}

function render(result) {
  $("#results").hidden = false;

  const meta = [];
  meta.push(`Channel ID: ${result.channelId || "—"}`);
  meta.push(`Videos: ${result.videoCount ?? "—"}`);
  meta.push(`Shorts detected: ${result.shortsCount ?? "—"}`);
  if (result?.global?.regionCode) meta.push(`Global region: ${result.global.regionCode}`);
  if (result?.global?.videoCategoryId) meta.push(`Category: ${result.global.videoCategoryId}`);
  $("#resultsMeta").textContent = meta.join(" • ");

  // chips
  chipList($("#tagsChips"), result?.suggested?.tags || [], "No tags");
  chipList($("#hashtagsChips"), result?.suggested?.hashtags || [], "No hashtags");
  chipList($("#shortsHashtagsChips"), result?.suggested?.shorts_hashtags || [], "No Shorts hashtags");
  chipList($("#globalHashtagsChips"), result?.suggested?.global_hashtags || [], "No global hashtags");

  // phrases
  phraseList($("#trendingPhrases"), result?.trendingPhrases || []);
  phraseList($("#trendingShortsPhrases"), result?.trendingShortsPhrases || []);

  // table
  tableVideos($("#topVideos"), result?.topVideos || []);

  // copy buttons
  $("#copyTagsBtn").onclick = () => {
    const s = (result.copy && result.copy.tagsComma) || (result?.suggested?.tags || []).join(", ");
    copyToClipboard(s);
  };
  $("#copyHashtagsBtn").onclick = () => {
    const s = (result.copy && result.copy.hashtags) || (result?.suggested?.hashtags || []).join(" ");
    copyToClipboard(s);
  };
  $("#copyShortsHashtagsBtn").onclick = () => {
    const s =
      (result.copy && result.copy.shortsHashtags) || (result?.suggested?.shorts_hashtags || []).join(" ");
    copyToClipboard(s);
  };
  $("#copyGlobalHashtagsBtn").onclick = () => {
    const s =
      (result.copy && result.copy.globalHashtags) || (result?.suggested?.global_hashtags || []).join(" ");
    copyToClipboard(s);
  };
  $("#copyAllBtn").onclick = () => copyToClipboard(buildCopyAll(result));
  $("#downloadJsonBtn").onclick = () => downloadJson(result);
}

function readForm() {
  const channel = ($("#channel")?.value || "").trim();
  const keywords = ($("#keywords")?.value || "").trim();
  const max_videos = Number(($("#max_videos")?.value || "100").trim());
  const region = ($("#region")?.value || "US").trim();
  const category_id = ($("#category_id")?.value || "").trim() || null;
  return {
    channel,
    keywords,
    max_videos: Number.isFinite(max_videos) ? max_videos : 100,
    region,
    category_id,
  };
}

function init() {
  initTheme();
  $("#themeToggle").onclick = () => {
    const cur = document.documentElement.getAttribute("data-theme") || "dark";
    setTheme(cur === "dark" ? "light" : "dark");
  };

  // Persist API base + format
  const savedApi = localStorage.getItem("trendtags_api_base");
  if (savedApi && $("#api_base")) $("#api_base").value = savedApi;
  const savedFmt = localStorage.getItem("trendtags_format");
  if (savedFmt === "csv" || savedFmt === "youtube") setFormat(savedFmt);

  document.querySelectorAll(".segmented__btn").forEach((b) => {
    b.onclick = () => {
      setFormat(b.dataset.format);
      localStorage.setItem("trendtags_format", outputFormat);
      toast(`Format: ${outputFormat.toUpperCase()}`);
    };
  });

  const form = $("#analyzeForm");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = readForm();
    if (!payload.channel) {
      toast("Please enter a channel identifier");
      return;
    }
    const apiBase = ($("#api_base")?.value || "").trim();
    localStorage.setItem("trendtags_api_base", apiBase);

    setBusy(true);
    try {
      const result = await analyze(payload);
      render(result);
      toast("Analysis complete");
      document.getElementById("results")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      toast(err?.message || "Analysis failed");
    } finally {
      setBusy(false);
    }
  });
}

init();

