/**
 * 李宁自动采集助手 v3.2 - Background Script
 * 改进：持久化存储 + 浏览器原生选择下载位置 + 解除采集数量上限
 */

console.log("[李宁v3.2 BG] 启动");

const store = {
  products: [],
  details: {},
  status: "就绪",
  autoDownload: true,
  downloadFolder: "lining_data",
  useSaveAs: true,  // 默认使用浏览器原生位置选择器
  collecting: false
};

async function initFromStorage() {
  try {
    const saved = await chrome.storage.local.get(["liningData", "liningSettings"]);
    if (saved.liningData) {
      store.products = saved.liningData.products || [];
      store.details = saved.liningData.details || {};
      store.status = saved.liningData.status || "就绪";
      store.collecting = false;
      console.log("[BG] 恢复数据:", store.products.length, "商品");
    }
    if (saved.liningSettings) {
      store.autoDownload = saved.liningSettings.autoDownload !== false;
      store.downloadFolder = saved.liningSettings.downloadFolder || "lining_data";
      store.useSaveAs = saved.liningSettings.useSaveAs !== false;
    }
  } catch (e) {
    console.log("[BG] 恢复失败:", e.message);
  }
}

async function persistToStorage() {
  try {
    await chrome.storage.local.set({
      liningData: {
        products: store.products,
        details: store.details,
        status: store.status
      }
    });
  } catch (e) {
    console.log("[BG] 持久化失败:", e.message);
  }
}

function broadcast(msg) {
  chrome.runtime.sendMessage(msg).catch(() => void chrome.runtime.lastError);
}

function generateCSV() {
  const products = store.products;
  const details = store.details;
  const headers = ["product_id", "name", "description", "category", "price", "image_url", "spu_id", "basic_info", "introduction", "detail_images", "source_url"];
  const rows = [headers];
  products.forEach(p => {
    const d = details[p.spu_id] || {};
    const description = [(d.basic_info || "").trim(), (d.introduction || "").trim()].filter(x => x).join("\n");
    rows.push([
      "lining_" + p.spu_id, p.name || "", description,
      p.category || d.category || "", d.price || p.price || "",
      p.image_url || "", p.spu_id, d.basic_info || "",
      d.introduction || "", (d.detail_images || []).join("|"), p.source_url || ""
    ]);
  });
  const BOM = "﻿";
  return BOM + rows.map(r => r.map(c => '"' + String(c).replace(/"/g, '""') + '"').join(",")).join("\n");
}

// 下载 CSV — 使用 callback 模式（兼容 Edge/Chrome）
function downloadCSV(trigger) {
  if (store.products.length === 0) return;
  const csv = generateCSV();
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const folder = store.downloadFolder || "lining_data";
  const sep = navigator.platform.includes("Win") ? "\\" : "/";
  const filename = folder + sep + "lining_" + store.products.length + "sku_" + ts + ".csv";
  const saveAs = store.useSaveAs;

  chrome.downloads.download({
    url: url,
    filename: filename,
    saveAs: saveAs,
    conflictAction: "uniquify"
  }, (downloadId) => {
    if (chrome.runtime.lastError) {
      console.log("[BG] 下载失败 (" + trigger + "):", chrome.runtime.lastError.message);
      store.status = "⚠️ 下载失败: " + chrome.runtime.lastError.message;
      broadcast({ action: "autoDownloadFailed", error: chrome.runtime.lastError.message });
    } else {
      console.log("[BG] 下载成功 (" + trigger + "), saveAs=" + saveAs + ", id=" + downloadId);
      if (!saveAs) {
        store.status = "✅ 已自动下载: " + filename;
        broadcast({ action: "autoDownloaded", filename: filename });
      } else {
        store.status = "✅ 已弹出保存窗口，请选择保存位置";
        broadcast({ action: "saveAsTriggered", filename: filename });
      }
    }
    setTimeout(() => URL.revokeObjectURL(url), 300000);
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  console.log("[BG] 收到:", msg.action);

  if (msg.action === "statusUpdate") {
    store.status = msg.status;
    store.collecting = true;
    broadcast({ action: "statusUpdate", status: msg.status });
    sendResponse({ ok: true });

  } else if (msg.action === "listReady") {
    store.products = msg.products;
    store.status = "列表完成: " + msg.products.length + " 个商品";
    persistToStorage();
    broadcast({ action: "productsUpdate", count: msg.products.length });
    sendResponse({ ok: true });

  } else if (msg.action === "detailReady") {
    if (msg.detail && msg.detail.spu_id) {
      store.details[msg.detail.spu_id] = msg.detail;
      const dc = Object.keys(store.details).length;
      store.status = "已采集详情 " + dc + "/" + store.products.length;
      broadcast({ action: "detailUpdate", count: dc });
      persistToStorage();

      // 全部完成 → 自动下载（如果开启了）
      if (store.products.length > 0 && dc >= store.products.length) {
        store.status = "✅ 全部完成: " + store.products.length + " 商品";
        store.collecting = false;
        broadcast({ action: "allDone", productCount: store.products.length });
        persistToStorage();
        if (store.autoDownload) downloadCSV("auto");
      }
    }
    sendResponse({ ok: true });

  } else if (msg.action === "getData") {
    sendResponse({
      products: store.products,
      details: store.details,
      status: store.status,
      detailCount: Object.keys(store.details).length,
      productCount: store.products.length,
      collecting: store.collecting,
      autoDownload: store.autoDownload,
      downloadFolder: store.downloadFolder,
      useSaveAs: store.useSaveAs
    });

  } else if (msg.action === "clearData") {
    store.products = [];
    store.details = {};
    store.status = "已清空";
    store.collecting = false;
    persistToStorage();
    sendResponse({ ok: true });

  } else if (msg.action === "clearDetail") {
    if (msg.spuId && store.details[msg.spuId]) {
      delete store.details[msg.spuId];
      persistToStorage();
    }
    sendResponse({ ok: true });

  } else if (msg.action === "openDetailTab") {
    chrome.tabs.create({ url: msg.url, active: false }, (tab) => {
      sendResponse({ tabId: tab ? tab.id : null });
    });
    return true;

  } else if (msg.action === "closeTab") {
    chrome.tabs.remove(msg.tabId, () => sendResponse({ ok: true }));
    return true;

  } else if (msg.action === "checkDetail") {
    const d = store.details[msg.spuId];
    sendResponse({ ready: !!d, detail: d || null });

  } else if (msg.action === "updateSettings") {
    if (msg.autoDownload !== undefined) store.autoDownload = msg.autoDownload;
    if (msg.downloadFolder !== undefined) store.downloadFolder = msg.downloadFolder;
    if (msg.useSaveAs !== undefined) store.useSaveAs = msg.useSaveAs;
    chrome.storage.local.set({
      liningSettings: {
        autoDownload: store.autoDownload,
        downloadFolder: store.downloadFolder,
        useSaveAs: store.useSaveAs
      }
    });
    sendResponse({ ok: true });

  } else if (msg.action === "manualExport") {
    downloadCSV("manual");
    sendResponse({ ok: true });

  } else if (msg.action === "testDownload") {
    testDownload().then(result => sendResponse(result));
    return true;
  }

  return true;
});

// 测试下载
async function testDownload() {
  const csv = "\uFEFFproduct_id,name,price\ntest_001,测试商品A,199\ntest_002,测试商品B,299\n";
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);

  return new Promise((resolve) => {
    chrome.downloads.download({
      url: url,
      filename: "test_export.csv",
      saveAs: true,
      conflictAction: "uniquify"
    }, (downloadId) => {
      if (chrome.runtime.lastError) {
        console.log("[BG-TEST] 失败:", chrome.runtime.lastError.message);
        URL.revokeObjectURL(url);
        resolve({ ok: false, error: chrome.runtime.lastError.message });
      } else {
        console.log("[BG-TEST] 成功, id=" + downloadId);
        setTimeout(() => URL.revokeObjectURL(url), 300000);
        resolve({ ok: true, id: downloadId });
      }
    });
  });
}


initFromStorage();

chrome.downloads.onChanged.addListener((delta) => {
  if (delta.state && delta.state.current === "complete") {
    console.log("[BG] 下载完成:", delta.id);
  }
});
