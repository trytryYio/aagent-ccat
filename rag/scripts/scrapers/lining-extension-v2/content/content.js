/** 李宁自动采集助手 v4.1 - Content Script
 * 核心：API 拦截（document_start 注入）拿数字 spuId，构造正确详情 URL
 */

if (window.__liningV4) {
  // 已加载
} else {
  window.__liningV4 = true;

  console.log('[李宁v4] 注入:', location.href);

  // ====== 立即拦截 API（document_start，在页面 JS 前）======
  const apiProducts = [];   // 从列表 API 拦截的商品
  const apiDetails = {};    // 从详情 API 拦截的详情（如果有）

  function cleanImage(item) {
    const spuList = item.spuVOList || [];
    if (spuList.length && spuList[0].primaryImage) {
      return String(spuList[0].primaryImage).split('?')[0];
    }
    const img = item.primaryImage || '';
    return img ? img.split('?')[0] : '';
  }

  // 解析分类（categoryPath 可能是对象数组 [{name:"男子"},{name:"跑鞋"}]）
  function parseCategory(item) {
    const cp = item.categoryPath || item.categoryName || item.categoryList || item.crumbs;
    if (Array.isArray(cp)) {
      return cp.map(c => typeof c === 'string' ? c : (c.name || c.categoryName || c.title || '')).filter(Boolean).join('/');
    }
    if (typeof cp === 'string') return cp;
    if (cp && typeof cp === 'object') return cp.name || cp.categoryName || '';
    return '';
  }

  function parseListItems(payload) {
    const data = (payload && payload.data) || payload;
    let items = [];
    if (data && data.data && Array.isArray(data.data.dataList)) items = data.data.dataList;
    else if (data && Array.isArray(data.dataList)) items = data.dataList;
    else if (data && data.data && Array.isArray(data.data.list)) items = data.data.list;

    items.forEach((item, idx) => {
      const spuId = String(item.spuId || '');
      if (!spuId) return;
      if (apiProducts.find(p => p.spu_id === spuId)) return;

      // 第一个商品打印完整结构（诊断用）
      if (apiProducts.length === 0 && idx === 0) {
        console.log('[李宁v4] 列表item字段:', Object.keys(item).join(','));
      }

      const priceInfo = item.spuPrice || {};
      const price = priceInfo.minSalePrice ? Math.floor(Number(priceInfo.minSalePrice) / 100) : 0;

      apiProducts.push({
        spu_id: spuId,
        name: item.title || '',
        price: price,
        image_url: cleanImage(item),
        category: parseCategory(item),
        source_url: 'https://store.lining.com/goods/detail?spuId=' + spuId
      });
    });
    console.log('[李宁v4] 列表API捕获累计:', apiProducts.length);
  }

  // 从 DOM 卡片提取兜底（首页/列表API没 hook 到时用）
  function parseDOMCards() {
    const selectors = [
      '.ui-goods-list-card',
      '[class*="goods-list-card"]',
      '[class*="GoodsListCard"]',
      '[class*="list-card"]',
      '[class*="product-card"]'
    ];
    let cards = [];
    for (const sel of selectors) {
      const nodeList = document.querySelectorAll(sel);
      if (nodeList.length) { cards = Array.from(nodeList); break; }
    }
    // 兜底：用包含 spuId 的链接找父级卡片
    if (cards.length === 0) {
      document.querySelectorAll('a[href*="spuId="]').forEach(a => {
        const card = a.closest('[class*="card"], [class*="item"], [class*="goods-list"]');
        if (card) cards.push(card);
      });
    }
    let added = 0;
    cards.forEach(card => {
      const link = card.querySelector('a[href*="spuId="]') || card.closest('a[href*="spuId="]');
      if (!link) return;
      const m = (link.getAttribute('href') || '').match(/spuId=(\d+)/);
      if (!m) return;
      const spuId = m[1];
      if (apiProducts.find(p => p.spu_id === spuId)) return;
      const img = card.querySelector('img');
      const titleEl = card.querySelector('[class*="title"], [class*="name"], [class*="goods-name"], h3, h4');
      const priceEl = card.querySelector('[class*="price"], [class*="salePrice"], [class*="sale-price"]');
      const price = priceEl ? parseFloat((priceEl.textContent.match(/[\d.]+/) || ['0'])[0]) : 0;
      apiProducts.push({
        spu_id: spuId,
        name: titleEl ? titleEl.textContent.trim() : '',
        price: price,
        image_url: img ? (img.getAttribute('src') || '').split('?')[0] : '',
        category: parseCategory({}),
        source_url: 'https://store.lining.com/goods/detail?spuId=' + spuId
      });
      added++;
    });
    if (added) console.log('[李宁v4] DOM卡片兜底新增:', added);
    return added;
  }

  // 通过 fetch 主动拉取列表 API（用于首页拦截失败时）
  async function fetchListApiFromUrl(pageUrl) {
    try {
      const u = new URL(pageUrl || location.href);
      const category = u.searchParams.get('category') || '';
      const labelCode = u.searchParams.get('labelCode') || '';
      const crumbs = u.searchParams.get('crumbs') || '';
      const field = u.searchParams.get('field') || 'sales_num';
      const sort = u.searchParams.get('sort') || '1';
      const pageNum = u.searchParams.get('pageNum') || '1';
      const apiUrl = 'https://api.store.lining.com/goods-jh-query/search/lining/list/page?' +
        'category=' + encodeURIComponent(category) +
        '&labelCode=' + encodeURIComponent(labelCode) +
        '&crumbs=' + encodeURIComponent(crumbs) +
        '&field=' + encodeURIComponent(field) +
        '&sort=' + encodeURIComponent(sort) +
        '&pageNum=' + encodeURIComponent(pageNum) +
        '&size=40';
      console.log('[李宁v4] fetch API:', apiUrl.substring(0, 120));
      const res = await fetch(apiUrl, { credentials: 'include' });
      if (!res.ok) return false;
      const data = await res.json();
      parseListItems(data);
      return true;
    } catch (e) {
      console.log('[李宁v4] 主动拉列表API失败:', e.message);
      return false;
    }
  }

  // 解析详情 API 数据（参考 lining_scraper_v3.py 的 _parse_detail_api）
  function handleDetailApi(payload) {
    const url = (payload && payload.url) || '';
    const raw = (payload && payload.data) || {};
    const d = raw.data || raw;
    if (!d || typeof d !== 'object') return;

    // spuId：从 url 或 data 提取
    let spuId = '';
    const um = url.match(/spuId=(\d+)/) || url.match(/detail\/(\d+)/);
    if (um) spuId = um[1];
    if (!spuId && d.spuId) spuId = String(d.spuId);
    if (!spuId && d.id) spuId = String(d.id);
    if (!spuId) return;

    // basic_info
    let basicInfo = '';
    const bi = d.basicInfo || d.goodsBasicInfo || d.attrs || d.goodsAttr;
    if (bi) basicInfo = typeof bi === 'string' ? bi : JSON.stringify(bi);

    // introduction
    const intro = d.introduction || d.description || d.goodsDesc || d.goodsIntroduce || '';

    // images
    let images = [];
    ['images', 'goodsImages', 'imageList', 'spuImages', 'detailImages', 'spuVOList', 'mainImages'].forEach(k => {
      if (images.length) return;
      const v = d[k];
      if (Array.isArray(v)) {
        images = v.map(img => typeof img === 'string' ? img : (img.url || img.imageUrl || img.primaryImage || img)).filter(s => typeof s === 'string' && s);
      }
    });

    // price
    let price = 0;
    const pi = d.price || d.spuPrice || {};
    if (pi && pi.minSalePrice) price = Math.floor(Number(pi.minSalePrice) / 100);
    else if (pi && pi.salePrice) price = Math.floor(Number(pi.salePrice) / 100);

    apiDetails[spuId] = {
      spu_id: spuId,
      name: d.title || d.name || d.goodsName || '',
      price: price,
      basic_info: basicInfo,
      introduction: typeof intro === 'string' ? intro : JSON.stringify(intro),
      detail_images: images,
      source_url: location.href
    };
    console.log('[李宁v4] 详情API解析:', spuId, '| basic', basicInfo.length, '| intro', String(intro).length, '| imgs', images.length);
  }

  // 监听主世界 inject.js 通过 postMessage 发来的 API 数据
  window.addEventListener('message', function (e) {
    if (!e.data || e.data.source !== 'lining-inject') return;
    if (e.data.type === 'list') {
      parseListItems(e.data.data);
    } else if (e.data.type === 'detail') {
      handleDetailApi(e.data.data);
    }
  });

  // ====== DOM ready 后初始化 ======
  function init() {
    // 实时判断（SPA 前端路由不刷新页面，init 时 URL 可能是首页）
    function isListUrl() {
      const u = location.href;
      return u.indexOf('/goods/list') >= 0 || u.indexOf('category=') >= 0 || u.indexOf('ap=') >= 0;
    }
    function isDetailUrl() {
      return location.href.indexOf('/goods/detail') >= 0;
    }

    let collecting = false; // 防重锁

    function sendToBg(msg) {
      try { chrome.runtime.sendMessage(msg, () => void chrome.runtime.lastError); } catch (e) {}
    }
    function bgCall(msg) {
      return new Promise(resolve => {
        try { chrome.runtime.sendMessage(msg, r => resolve(r)); } catch (e) { resolve(null); }
      });
    }
    function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

    chrome.runtime.onMessage.addListener((req, sender, sendResponse) => {
      console.log('[李宁v4] 收到:', req.action, '| 当前URL:', location.href.substring(0, 50));
      if (req.action === 'startAutoCollect') {
        if (collecting) {
          console.log('[李宁v4] 已在采集，忽略重复');
        } else if (isListUrl()) {
          collectListPage(req.limit);
        } else {
          console.log('[李宁v4] 当前非列表页，请先打开列表页');
          sendToBg({ action: 'statusUpdate', status: '请在列表页开始采集' });
        }
        sendResponse({ ok: true });
      } else if (req.action === 'diagnose') {
        sendResponse({
          url: location.href,
          apiProducts: apiProducts.length,
          cards: document.querySelectorAll('.ui-goods-list-card').length
        });
      } else if (req.action === 'fallbackDownload') {
        // 回退下载：用 <a> 标签触发浏览器下载
        try {
          const blob = new Blob([req.csv], { type: "text/csv;charset=utf-8" });
          const aUrl = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = aUrl;
          a.download = req.filename.split(/[/\\]/).pop();
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          setTimeout(() => URL.revokeObjectURL(aUrl), 10000);
          console.log('[李宁v4] 回退下载已触发');
        } catch (e) {
          console.error('[李宁v4] 回退下载失败:', e);
        }
        sendResponse({ ok: true });
      }
      return true;
    });

    // 找「下一页」按钮（多种选择器）
    function findNextPageBtn() {
      const selectors = [
        '.ant-pagination-next',
        '[class*="pagination-next"]', '[class*="PaginationNext"]',
        '[class*="next-page"]', '[class*="nextPage"]', '[class*="next_page"]',
        'li[class*="next"]', 'button[class*="next"]',
        '[class*="page-next"]', '[class*="PageNext"]'
      ];
      for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el && el.offsetHeight > 0 && !el.disabled && !el.className.match(/disabled|disable/i)) {
          return el;
        }
      }
      // 文字兜底：在分页区域找「下一页」/>/›
      const pagi = document.querySelector('[class*="pagination"],[class*="Pagination"],[class*="pageWrap"],[class*="pager"]');
      if (pagi) {
        const all = pagi.querySelectorAll('*');
        for (const el of all) {
          const t = (el.textContent || '').trim();
          if (el.offsetHeight > 0 && (t === '下一页' || t === '>' || t === '›' || t === '»' || t === 'Next')) {
            return el;
          }
        }
      }
      return null;
    }

    // ====== 列表采集（翻页模式，不滚动）======
    async function collectListPage(target) {
      collecting = true;
      try {
        console.log('[李宁v4] === 列表采集开始（翻页模式）===');
        sendToBg({ action: 'statusUpdate', status: '采集首页...' });

        // 1. 等首页 API 被拦截（最多 8 秒），同时 DOM 兜底
        for (let w = 0; w < 8 && apiProducts.length === 0; w++) {
          await sleep(1000);
          if (w >= 2 && apiProducts.length === 0) parseDOMCards();
        }
        // 如果 API 仍为空，主动拉取一次列表 API（解决首页不触发请求的问题）
        if (apiProducts.length === 0) {
          console.log('[李宁v4] 首页API未捕获，尝试主动拉取...');
          const ok = await fetchListApiFromUrl(location.href);
          if (ok) console.log('[李宁v4] 主动拉取首页API成功:', apiProducts.length);
          else parseDOMCards();
        }
        console.log('[李宁v4] 首页 API:', apiProducts.length, '个商品');

        // 2. 翻页采集（目标数量由用户指定，每页约25）
        const TARGET = target || 50;
        let page = 1;
        while (apiProducts.length < TARGET && page < 80) {
          const before = apiProducts.length;

          // 翻页策略：优先点按钮，失败则直接 fetch API
          let gotNew = false;

          // 策略1: 点「下一页」按钮
          const btn = findNextPageBtn();
          if (btn) {
            btn.click();
            console.log(`[李宁v4] 翻到第 ${page + 1} 页（点击按钮）`);
            sendToBg({ action: 'statusUpdate', status: `翻到第${page + 1}页（已${apiProducts.length}个）` });
            for (let w = 0; w < 10; w++) {
              await sleep(1000);
              if (apiProducts.length > before) { gotNew = true; break; }
              if (w >= 3 && apiProducts.length === before) parseDOMCards();
            }
          }

          // 策略2: 直接 fetch API（按钮失败或不存在时）
          if (!gotNew) {
            console.log(`[李宁v4] 按钮翻页未生效，尝试直接 fetch 第${page + 1}页 API`);
            try {
              const u = new URL(location.href);
              u.searchParams.set('pageNum', String(page + 1));
              await fetchListApiFromUrl(u.toString());
              if (apiProducts.length > before) gotNew = true;
            } catch (e) {
              console.log('[李宁v4] fetch API 翻页失败:', e.message);
            }
          }

          // 策略3: 改 URL + 触发 popstate（最后兜底）
          if (!gotNew) {
            try {
              const u = new URL(location.href);
              u.searchParams.set('pageNum', String(page + 1));
              history.pushState({}, '', u.toString());
              window.dispatchEvent(new PopStateEvent('popstate'));
              for (let w = 0; w < 8; w++) {
                await sleep(1000);
                if (apiProducts.length > before) { gotNew = true; break; }
              }
            } catch (e) {}
          }

          page++;
          console.log(`[李宁v4] 第${page}页后累计: ${apiProducts.length} 个`);
          if (!gotNew) {
            console.log('[李宁v4] 翻页无新数据，停止');
            break;
          }
        }

        const products = apiProducts.slice(0, TARGET);
        console.log('[李宁v4] 列表采集完成:', products.length, '个商品');
        sendToBg({ action: 'listReady', products: products });

        if (products.length === 0) {
          sendToBg({ action: 'statusUpdate', status: '失败: API未捕获到商品' });
          return;
        }

        await collectAllDetails(products);
      } catch (e) {
        console.error('[李宁v4] 采集异常:', e);
      } finally {
        collecting = false;
      }
    }

    // ====== 详情采集循环（content.js 驱动，不依赖 background 保活）======
    async function collectAllDetails(products) {
      console.log('[李宁v4] === 开始逐个采集详情 ===');
      sendToBg({ action: 'statusUpdate', status: `采集详情 0/${products.length}` });
      for (let i = 0; i < products.length; i++) {
        const p = products[i];
        sendToBg({ action: 'statusUpdate', status: `详情 ${i + 1}/${products.length}: ${(p.name || '').substring(0, 15)}` });
        console.log(`[李宁v4] [${i + 1}/${products.length}] ${p.spu_id} ${(p.name || '').substring(0, 25)}`);
        await collectOneDetail(p);
        await sleep(1000);
      }

      // 第一轮结束后，针对失败的商品再回填一次
      await retryFailedDetails(products);

      sendToBg({ action: 'statusUpdate', status: `完成! ${products.length}商品` });
      sendToBg({ action: 'allDone', productCount: products.length });
      console.log('[李宁v4] === 全部完成 ===');
    }

    // 详情回填：第一轮失败（仍为空）的 sku，集中再跑一次
    async function retryFailedDetails(products) {
      const isValid = (d) => d && ((d.introduction || '').length > 5 || (d.basic_info || '').length > 5 || (d.detail_images || []).length > 0);
      const failed = [];
      for (const p of products) {
        const res = await bgCall({ action: 'checkDetail', spuId: p.spu_id });
        if (!res || !res.ready || !isValid(res.detail)) failed.push(p);
      }
      if (failed.length === 0) {
        console.log('[李宁v4] 第一轮详情全部成功，无需回填');
        return;
      }
      console.log(`[李宁v4] 第一轮失败 ${failed.length} 个，开始回填...`, failed.map(p => p.spu_id));
      sendToBg({ action: 'statusUpdate', status: `回填详情 0/${failed.length}` });
      for (let i = 0; i < failed.length; i++) {
        const p = failed[i];
        sendToBg({ action: 'statusUpdate', status: `回填 ${i + 1}/${failed.length}: ${(p.name || '').substring(0, 15)}` });
        console.log(`[李宁v4] [回填 ${i + 1}/${failed.length}] ${p.spu_id} ${(p.name || '').substring(0, 25)}`);
        await collectOneDetail(p, true);
        await sleep(2000); // 回填时间间隔更长，降低网络波动影响
      }
      console.log('[李宁v4] 回填完成');
    }

    async function collectOneDetail(product, isBackfill = false) {
      // 详情有效 = intro/basic/imgs 至少一个有内容（用于判断是否需重试）
      const isValid = (d) => d && ((d.introduction || '').length > 5 || (d.basic_info || '').length > 5 || (d.detail_images || []).length > 0);

      const MAX_ATTEMPT = isBackfill ? 5 : 4; // 回填时多给一次重试
      const WAIT_MS = 800;
      const MAX_WAIT = isBackfill ? 55 : 45;  // 回填时单次等待更长

      for (let attempt = 0; attempt < MAX_ATTEMPT; attempt++) {
        // 重试前清旧（空）详情，让新标签重新采集
        if (attempt > 0) await bgCall({ action: 'clearDetail', spuId: product.spu_id });

        const tabRes = await bgCall({ action: 'openDetailTab', url: product.source_url });
        const tabId = tabRes && tabRes.tabId;
        if (!tabId) { console.warn('[李宁v4] 开标签失败', product.source_url); return; }

        // 轮询等详情
        let gotDetail = null;
        for (let w = 0; w < MAX_WAIT; w++) {
          await sleep(WAIT_MS);
          const res = await bgCall({ action: 'checkDetail', spuId: product.spu_id });
          if (res && res.ready) { gotDetail = res.detail; break; }
        }
        await bgCall({ action: 'closeTab', tabId: tabId });

        if (isValid(gotDetail)) {
          console.log(`[李宁v4] ✅ ${product.spu_id}${attempt > 0 ? '(重试成功)' : ''}${isBackfill ? '【回填】' : ''} | basic=${(gotDetail.basic_info || '').length} intro=${(gotDetail.introduction || '').length} imgs=${(gotDetail.detail_images || []).length}`);
          return;
        }
        console.log(`[李宁v4] ${product.spu_id} 第${attempt + 1}次详情为空${attempt < MAX_ATTEMPT - 1 ? '，重试中' : '，放弃'}${isBackfill ? '【回填】' : ''}`);
        await sleep(isBackfill ? 2500 : 1500); // 回填时重试间隔更长
      }
      console.log(`[李宁v4] ⚠️ 超时 ${product.spu_id}${isBackfill ? '【回填】' : ''}`);
    }

    // ====== 详情页：自动采集并发送（background 开新 tab 时触发）======
    if (isDetailUrl()) {
      console.log('[李宁v4] 详情页，轮询采集...');
      setTimeout(async () => {
        const spuId = (location.href.match(/spuId=(\d+)/) || [])[1] || '';
        const isEmpty = (d) => !d || ((!d.basic_info || d.basic_info.length < 5) && (!d.introduction || d.introduction.length < 5) && (d.detail_images || []).length === 0);

        // 轮询采集：启动 0.8s 后，每 1s 试一次，最多 30 秒，采到非空就停（解决慢加载详情页）
        let detail = null;
        for (let i = 0; i < 30; i++) {
          // 渐进滚动触发懒加载 + SPA 渲染（详情图文常懒渲染）
          try {
            const h = document.body && document.body.scrollHeight || 0;
            if (h) window.scrollTo(0, h * (i + 1) / 30);
            // 尝试点击「商品详情/详情」tab 触发懒渲染
            if (i === 4 || i === 8) {
              const tabs = document.querySelectorAll('[class*="tab"], [class*="Tab"], [role="tab"]');
              for (const t of tabs) {
                const txt = (t.textContent || '').trim();
                if (/详情|介绍|图文|参数|商品信息/.test(txt)) { t.click(); break; }
              }
            }
          } catch (e) {}
          await sleep(1000);
          detail = extractDetailFromDOM();
          if (!isEmpty(detail)) {
            console.log(`[李宁v4] 详情页第${i + 1}次轮询采到内容`);
            break;
          }
        }

        // 详情 API 作为补充（合并空字段）
        if (apiDetails[spuId] && detail) {
          const api = apiDetails[spuId];
          detail.basic_info = (detail.basic_info || '').length > 5 ? detail.basic_info : (api.basic_info || '');
          detail.introduction = (detail.introduction || '').length > 5 ? detail.introduction : (api.introduction || '');
          detail.detail_images = (detail.detail_images && detail.detail_images.length) ? detail.detail_images : (api.detail_images || []);
          detail.name = detail.name || api.name;
          detail.price = detail.price || api.price;
        } else if (apiDetails[spuId] && !detail) {
          detail = apiDetails[spuId];
        }

        if (detail) {
          sendToBg({ action: 'detailReady', detail: detail });
          console.log('[李宁v4] 详情已发送:', detail.spu_id,
            '| basic', (detail.basic_info || '').length, '| intro', (detail.introduction || '').length, '| imgs', (detail.detail_images || []).length);
        }
      }, 800);
    }

    function extractDetailFromDOM() {
      let spuId = '';
      const m = location.href.match(/spuId=(\d+)/);
      if (m) spuId = m[1];

      let name = '';
      ['.goods-title', '.goods-name', '[class*="goodsName"]', 'h1', '[class*="title"]'].forEach(sel => {
        if (name) return;
        const el = document.querySelector(sel);
        if (el && el.textContent.trim().length > 5) name = el.textContent.trim().replace(/￥[\d.]+/g, '').trim();
      });

      let price = 0;
      const priceEl = document.querySelector('[class*="price"], .goods-price, .activity-price');
      if (priceEl) {
        const pt = priceEl.textContent.match(/[\d.]+/);
        if (pt) price = parseFloat(pt[0]);
      }

      let basicInfo = '';
      ['[class*="basic___"]', '[class*="basicInfo"]', '[class*="goodsInfo"]', '.goods-info', '[class*="attr"]'].forEach(sel => {
        if (basicInfo.length > 10) return;
        const el = document.querySelector(sel);
        if (el) basicInfo = el.textContent.trim().substring(0, 2000);
      });

      let intro = '';
      ['[class*="introduce___"]', '[class*="introduce"]', '[class*="intro"]', '.goods-desc', '[class*="desc"]', '[class*="content___"]'].forEach(sel => {
        if (intro.length > 20) return;
        const el = document.querySelector(sel);
        if (el) intro = el.textContent.trim().substring(0, 2000);
      });

      // 用 URL 特征提取商品图（懒加载图 naturalWidth=0，不能用尺寸过滤）
      const detailImages = [];
      const isGoodsImg = (src) =>
        /lining-goods|data\/lining|\/goods\/|\/dp\/|lining-online/i.test(src);
      const isJunk = (src) =>
        /icon|logo|avatar|qrcode|sprite|placeholder|loading|favicon|\.svg/i.test(src);
      document.querySelectorAll('img').forEach(img => {
        let src = img.getAttribute('src') || img.getAttribute('data-src')
          || img.getAttribute('data-lazy-src') || img.getAttribute('data-lazy') || '';
        if (src.indexOf('http') < 0 || src.indexOf('data:') >= 0) return;
        if (isJunk(src)) return;
        if (isGoodsImg(src)) detailImages.push(src.split('?')[0]);  // 去 URL 参数便于去重
      });
      // 兜底：URL 特征没命中，取所有 http 图（排除 junk）
      if (detailImages.length === 0) {
        document.querySelectorAll('img').forEach(img => {
          const src = (img.getAttribute('src') || img.getAttribute('data-src') || '').split('?')[0];
          if (src.indexOf('http') >= 0 && src.indexOf('data:') < 0 && !isJunk(src)) {
            detailImages.push(src);
          }
        });
      }

      return {
        spu_id: spuId,
        name: name,
        price: price,
        basic_info: basicInfo,
        introduction: intro,
        detail_images: [...new Set(detailImages)].slice(0, 30),
        source_url: location.href
      };
    }

    console.log('[李宁v4] 就绪 (list=' + isListUrl() + ' detail=' + isDetailUrl() + ' | ' + location.href.substring(0, 40) + ')');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
}



