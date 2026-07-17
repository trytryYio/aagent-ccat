/**
 * 李宁官网数据采集 - Content Script
 * 
 * 策略：
 *   1. 拦截列表 API 响应
 *   2. 拦截详情 API 响应
 *   3. 提取商品数据
 *   4. 发送给 popup
 */

console.log('[李宁采集] Content script 加载');

// ==================== 数据存储 ====================

let capturedProducts = [];
let capturedDetails = {};
let currentMode = 'list'; // 'list' or 'detail'

// ==================== API 拦截 ====================

function interceptApi() {
  // 拦截 fetch
  const originalFetch = window.fetch;
  window.fetch = async function(...args) {
    const response = await originalFetch.apply(this, args);
    const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
    
    try {
      // 列表 API
      if (url.includes('goods-jh-query/search/lining/list/page')) {
        const clone = response.clone();
        const data = await clone.json();
        handleListResponse(data);
      }
      
      // 详情 API (多种可能的路径)
      if (url.includes('lining/detail') || url.includes('goods/detail') || url.includes('spu/detail') || url.includes('goodsInfo')) {
        const clone = response.clone();
        const data = await clone.json();
        handleDetailResponse(data);
      }
    } catch (e) {
      console.warn('[李宁采集] API 拦截失败:', e);
    }
    
    return response;
  };

  // 拦截 XHR
  const originalXHROpen = XMLHttpRequest.prototype.open;
  const originalXHRSend = XMLHttpRequest.prototype.send;
  
  XMLHttpRequest.prototype.open = function(method, url) {
    this._url = url;
    return originalXHROpen.apply(this, arguments);
  };
  
  XMLHttpRequest.prototype.send = function(...args) {
    this.addEventListener('load', function() {
      const url = this._url || '';
      try {
        if (url.includes('goods-jh-query/search/lining/list/page')) {
          const data = JSON.parse(this.responseText);
          handleListResponse(data);
        }
        if (url.includes('lining/detail') || url.includes('goods/detail') || url.includes('spu/detail')) {
          const data = JSON.parse(this.responseText);
          handleDetailResponse(data);
        }
      } catch (e) {}
    });
    return originalXHRSend.apply(this, arguments);
  };
}

// ==================== 列表响应处理 ====================

function handleListResponse(data) {
  console.log('[李宁采集] 捕获列表 API:', data);
  
  const items = data?.data?.dataList || data?.data?.list || [];
  
  if (!items || items.length === 0) {
    console.log('[李宁采集] 列表为空');
    return;
  }
  
  items.forEach(item => {
    const spuId = item.spuId || item.id || '';
    if (!spuId) return;
    
    // 提取主要图片
    const primaryImage = item.spuVOList?.[0]?.primaryImage || item.primaryImage || '';
    const images = item.spuVOList?.[0]?.images || item.images || [];
    
    const product = {
      spu_id: spuId,
      title: item.title || item.name || '',
      price: item.spuPrice?.minSalePrice ? item.spuPrice.minSalePrice / 100 : 0,
      category: item.categoryPath || item.categoryName || '',
      gender: item.gender || '',
      series: item.series || '',
      image_url: primaryImage,
      images: images.map(img => img.url || img),
      source_url: `https://store.lining.com/goods/detail/${spuId}`,
      source: 'list_api',
      captured_at: new Date().toISOString()
    };
    
    // 去重
    if (!capturedProducts.find(p => p.spu_id === spuId)) {
      capturedProducts.push(product);
      console.log(`[李宁采集] 捕获商品: ${product.title}`);
    }
  });
  
  // 通知 popup
  notifyPopup({
    action: 'listUpdate',
    count: capturedProducts.length,
    products: capturedProducts
  });
}

// ==================== 详情响应处理 ====================

function handleDetailResponse(data) {
  console.log('[李宁采集] 捕获详情 API:', data);
  
  const detail = data?.data || data;
  if (!detail) return;
  
  const spuId = detail.spuId || detail.id || '';
  if (!spuId) return;
  
  // 提取详情信息
  const detailInfo = {
    spu_id: spuId,
    basic_info: '',
    introduction: detail.introduction || detail.description || '',
    detail_images: [],
    price: null,
    source_url: `https://store.lining.com/goods/detail/${spuId}`,
    source: 'detail_api',
    captured_at: new Date().toISOString()
  };
  
  // 基础信息
  if (detail.basicInfo) {
    detailInfo.basic_info = typeof detail.basicInfo === 'string' 
      ? detail.basicInfo 
      : JSON.stringify(detail.basicInfo);
  } else if (detail.goodsBasicInfo) {
    detailInfo.basic_info = typeof detail.goodsBasicInfo === 'string'
      ? detail.goodsBasicInfo
      : JSON.stringify(detail.goodsBasicInfo);
  }
  
  // 详情图片
  const imageKeys = ['images', 'goodsImages', 'imageList', 'spuImages', 'detailImages'];
  for (const key of imageKeys) {
    if (detail[key] && Array.isArray(detail[key])) {
      detailInfo.detail_images = detail[key].map(img => img.url || img.imageUrl || img).filter(Boolean);
      if (detailInfo.detail_images.length > 0) break;
    }
  }
  
  // 价格
  const priceInfo = detail.price || detail.spuPrice || {};
  if (typeof priceInfo === 'object') {
    const minPrice = priceInfo.minSalePrice || priceInfo.salePrice;
    if (minPrice) {
      detailInfo.price = Math.floor(parseInt(minPrice) / 100);
    }
  }
  
  capturedDetails[spuId] = detailInfo;
  console.log(`[李宁采集] 捕获详情: ${spuId}`, detailInfo);
  
  notifyPopup({
    action: 'detailUpdate',
    spuId: spuId,
    detail: detailInfo,
    totalDetails: Object.keys(capturedDetails).length
  });
}

// ==================== 通知 Popup ====================

function notifyPopup(message) {
  try {
    chrome.runtime.sendMessage(message);
  } catch (e) {
    // popup 可能未打开
  }
}

// ==================== 响应 Popup 请求 ====================

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'getProducts') {
    sendResponse({
      products: capturedProducts,
      details: capturedDetails
    });
  } else if (request.action === 'clearData') {
    capturedProducts = [];
    capturedDetails = {};
    sendResponse({ success: true });
  } else if (request.action === 'getStatus') {
    sendResponse({
      productCount: capturedProducts.length,
      detailCount: Object.keys(capturedDetails).length,
      url: window.location.href
    });
  }
  return true;
});

// ==================== 启动 ====================

try {
  interceptApi();
  console.log('[李宁采集] API 拦截器已启动');
} catch (e) {
  console.error('[李宁采集] 启动失败:', e);
}
