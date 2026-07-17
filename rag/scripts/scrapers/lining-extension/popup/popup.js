/**
 * 李宁商品数据采集 - Popup Script
 */

let products = [];
let details = {};

// ==================== 初始化 ====================

async function init() {
  // 获取当前 tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  
  if (!tab || !tab.url || !tab.url.includes('store.lining.com')) {
    document.getElementById('currentPage').textContent = '非李宁官网';
    document.getElementById('exportBtn').disabled = true;
    document.getElementById('exportJsonBtn').disabled = true;
    return;
  }
  
  // 显示当前页面类型
  if (tab.url.includes('/goods/list')) {
    document.getElementById('currentPage').textContent = '列表页';
  } else if (tab.url.includes('/goods/detail')) {
    document.getElementById('currentPage').textContent = '详情页';
  }
  
  // 获取捕获的数据
  try {
    const response = await chrome.tabs.sendMessage(tab.id, { action: 'getProducts' });
    if (response) {
      products = response.products || [];
      details = response.details || {};
      updateStats();
    }
  } catch (e) {
    console.log('获取数据失败:', e);
  }
}

// ==================== 更新统计 ====================

function updateStats() {
  document.getElementById('productCount').textContent = products.length;
  document.getElementById('detailCount').textContent = Object.keys(details).length;
}

// ==================== 监听 content script 更新 ====================

chrome.runtime.onMessage.addListener((message) => {
  if (message.action === 'listUpdate') {
    products = message.products || [];
    updateStats();
  } else if (message.action === 'detailUpdate') {
    details[message.spuId] = message.detail;
    updateStats();
  }
});

// ==================== 导出 CSV ====================

document.getElementById('exportBtn').addEventListener('click', () => {
  if (products.length === 0) {
    alert('没有数据可导出！请先浏览商品列表。');
    return;
  }
  
  // 合并商品列表和详情
  const mergedData = products.map(p => {
    const detail = details[p.spu_id] || {};
    return {
      product_id: `lining_${p.spu_id}`,
      name: p.title,
      description: detail.basic_info || '',
      category: p.category,
      price: detail.price || p.price,
      image_url: p.image_url,
      gender: p.gender,
      series: p.series,
      spu_id: p.spu_id,
      basic_info: detail.basic_info || '',
      introduction: detail.introduction || '',
      detail_images: (detail.detail_images || []).join('|'),
      source_url: p.source_url
    };
  });
  
  // 生成 CSV
  const headers = Object.keys(mergedData[0]);
  const csvRows = [
    headers.join(','),
    ...mergedData.map(row => 
      headers.map(h => {
        let val = row[h] ?? '';
        // 转义逗号和引号
        if (typeof val === 'string' && (val.includes(',') || val.includes('"') || val.includes('\n'))) {
          val = '"' + val.replace(/"/g, '""') + '"';
        }
        return val;
      }).join(',')
    )
  ];
  
  const csv = csvRows.join('\n');
  downloadFile(csv, `lining_products_${Date.now()}.csv`, 'text/csv');
});

// ==================== 导出 JSON ====================

document.getElementById('exportJsonBtn').addEventListener('click', () => {
  if (products.length === 0) {
    alert('没有数据可导出！请先浏览商品列表。');
    return;
  }
  
  const exportData = {
    products: products,
    details: details,
    exported_at: new Date().toISOString()
  };
  
  const json = JSON.stringify(exportData, null, 2);
  downloadFile(json, `lining_data_${Date.now()}.json`, 'application/json');
});

// ==================== 下载文件 ====================

function downloadFile(content, filename, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  
  chrome.downloads.download({
    url: url,
    filename: filename,
    saveAs: true
  }, () => {
    URL.revokeObjectURL(url);
  });
}

// ==================== 清空数据 ====================

document.getElementById('clearBtn').addEventListener('click', async () => {
  if (!confirm('确定清空所有捕获的数据？')) return;
  
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab) {
    try {
      await chrome.tabs.sendMessage(tab.id, { action: 'clearData' });
    } catch (e) {}
  }
  
  products = [];
  details = {};
  updateStats();
});

// ==================== 启动 ====================

init();
