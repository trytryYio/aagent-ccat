/**
 * 李宁自动采集助手 v3.2 - Popup
 * 改进：支持浏览器原生位置选择器 + 无上限采集数量
 */

let pollTimer = null;

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  if (!tab || !tab.url || !tab.url.includes("lining.com")) {
    document.getElementById("currentPage").textContent = "非李宁官网";
    document.getElementById("startBtn").disabled = true;
  } else if (tab.url.includes("/goods/list")) {
    document.getElementById("currentPage").textContent = "列表页";
  } else if (tab.url.includes("/goods/detail")) {
    document.getElementById("currentPage").textContent = "详情页";
  } else {
    document.getElementById("currentPage").textContent = "其他页面";
  }

  // 恢复设置
  try {
    const settings = await chrome.storage.local.get("liningSettings");
    if (settings.liningSettings) {
      document.getElementById("autoDownloadChk").checked = settings.liningSettings.autoDownload !== false;
      document.getElementById("useSaveAsChk").checked = settings.liningSettings.useSaveAs !== false;
      document.getElementById("folderInput").value = settings.liningSettings.downloadFolder || "lining_data";
    }
  } catch (e) {}

  refresh();
}

async function refresh() {
  try {
    const data = await chrome.runtime.sendMessage({ action: "getData" });
    if (!data) return;

    document.getElementById("productCount").textContent = data.productCount || 0;
    document.getElementById("detailCount").textContent = data.detailCount || 0;

    const status = data.status || "就绪";
    const statusBox = document.getElementById("statusBox");
    const isDone = status.includes("✅") && (data.productCount || 0) > 0;
    const isCollecting = data.collecting && !isDone;
    const isError = status.includes("⚠️") || status.includes("失败");

    statusBox.classList.toggle("collecting", isCollecting);
    statusBox.classList.toggle("done", isDone);
    statusBox.classList.toggle("error", isError);

    if (isDone) {
      statusBox.textContent = status;
      document.getElementById("startBtn").style.display = "block";
      document.getElementById("stopBtn").style.display = "none";
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    } else if (isError) {
      statusBox.textContent = status;
      document.getElementById("startBtn").style.display = "block";
      document.getElementById("stopBtn").style.display = "none";
    } else {
      statusBox.textContent = status;
    }
  } catch (e) {}
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.action === "productsUpdate" || msg.action === "detailUpdate" || msg.action === "allDone") {
    refresh();
  }
  if (msg.action === "autoDownloaded") {
    const tip = document.getElementById("tipBox");
    tip.textContent = "✅ 已自动下载: " + msg.filename;
    tip.classList.remove("warn");
  }
  if (msg.action === "saveAsTriggered") {
    const tip = document.getElementById("tipBox");
    tip.textContent = "📂 已弹出保存窗口，请选择保存位置";
    tip.classList.remove("warn");
  }
  if (msg.action === "autoDownloadFailed") {
    const tip = document.getElementById("tipBox");
    tip.textContent = "⚠️ 自动下载失败，请点击「手动导出 CSV」";
    tip.classList.add("warn");
  }
});

async function saveSettings() {
  const autoDownload = document.getElementById("autoDownloadChk").checked;
  const useSaveAs = document.getElementById("useSaveAsChk").checked;
  const downloadFolder = document.getElementById("folderInput").value.trim() || "lining_data";
  await chrome.runtime.sendMessage({
    action: "updateSettings",
    autoDownload: autoDownload,
    useSaveAs: useSaveAs,
    downloadFolder: downloadFolder
  });
}

document.getElementById("autoDownloadChk").addEventListener("change", saveSettings);
document.getElementById("useSaveAsChk").addEventListener("change", saveSettings);
document.getElementById("folderInput").addEventListener("change", saveSettings);

document.getElementById("startBtn").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return;

  await saveSettings();

  document.getElementById("startBtn").style.display = "none";
  document.getElementById("stopBtn").style.display = "block";

  try {
    await chrome.runtime.sendMessage({ action: "clearData" });
    const limit = parseInt(document.getElementById("limitInput").value, 10) || 500;
    await chrome.tabs.sendMessage(tab.id, { action: "startAutoCollect", limit: limit });
  } catch (e) {
    alert("启动失败: " + e.message + "\n请先 F5 刷新页面");
  }

  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(refresh, 1000);
});

document.getElementById("stopBtn").addEventListener("click", () => {
  document.getElementById("startBtn").style.display = "block";
  document.getElementById("stopBtn").style.display = "none";
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
});

document.getElementById("exportBtn").addEventListener("click", async () => {
  await saveSettings();
  await chrome.runtime.sendMessage({ action: "manualExport" });
});

document.getElementById("clearBtn").addEventListener("click", async () => {
  if (!confirm("确定清空所有已采集的数据？")) return;
  await chrome.runtime.sendMessage({ action: "clearData" });
  refresh();
});

document.getElementById("diagnoseBtn").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    const result = await chrome.tabs.sendMessage(tab.id, { action: "diagnose" });
    alert(JSON.stringify(result, null, 2));
  } catch (e) {
    alert("诊断失败: " + e.message);
  }
});

document.getElementById("injectBtn").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content/content.js"]
    });
    alert("注入成功！");
  } catch (e) {
    alert("注入失败: " + e.message);
  }
});

// ========== 测试导出按钮 ==========
document.getElementById("testExportBtn").addEventListener("click", async () => {
  const tip = document.getElementById("tipBox");
  const ua = navigator.userAgent;
  const isEdge = ua.includes("Edg/");
  tip.textContent = "测试中... (浏览器: " + (isEdge ? "Edge" : "Chrome") + ")";
  tip.classList.remove("warn");

  // === 方法1: chrome.downloads.download + saveAs ===
  try {
    const testCsv = "\uFEFFproduct_id,name,price\n" +
      "test_001,测试商品A,199\n" +
      "test_002,测试商品B,299\n" +
      "test_003,测试商品C,399\n";

    const blob = new Blob([testCsv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);

    // Edge/Chrome 的 downloads API
    if (chrome.downloads) {
      const downloadId = await new Promise((resolve, reject) => {
        chrome.downloads.download({
          url: url,
          filename: "test_export.csv",
          saveAs: true,
          conflictAction: "uniquify"
        }, (id) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
          } else {
            resolve(id);
          }
        });
      });

      tip.textContent = "downloads API 成功! id=" + downloadId + " (请查看是否弹出保存窗口)";
      tip.classList.remove("warn");
      setTimeout(() => URL.revokeObjectURL(url), 300000);
      console.log("[测试] 方法1成功, id=" + downloadId);
      return;
    }
    URL.revokeObjectURL(url);
  } catch (e) {
    console.log("[测试] 方法1失败:", e.message);
  }

  // === 方法2: <a download> 直接触发 ===
  try {
    const testCsv = "\uFEFFproduct_id,name,price\ntest_001,测试商品A,199\ntest_002,测试商品B,299\n";
    const blob = new Blob([testCsv], { type: "text/csv;charset=utf-8" });
    const aUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = aUrl;
    a.download = "test_export.csv";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    tip.textContent = "已触发 <a> 下载 (请查看浏览器下载栏)";
    tip.classList.remove("warn");
    setTimeout(() => URL.revokeObjectURL(aUrl), 10000);
    console.log("[测试] 方法2成功");
    return;
  } catch (e) {
    console.log("[测试] 方法2失败:", e.message);
  }

  tip.textContent = "所有方法均失败! 请打开 Edge 扩展管理页检查权限";
  tip.classList.add("warn");
});

init();
