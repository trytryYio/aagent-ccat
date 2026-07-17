/**
 * 李宁采集 - 注入到页面主世界 (MAIN world)
 * hook fetch/XHR（列表+详情 API），带自检重 hook 防被覆盖
 */
(function () {
  if (window.__liningInject) return;
  window.__liningInject = true;

  // 保存原生引用（只一次，重 hook 时复用，避免嵌套）
  var NATIVE_FETCH = window.fetch;
  var NATIVE_XHR_OPEN = XMLHttpRequest.prototype.open;
  var NATIVE_XHR_SEND = XMLHttpRequest.prototype.send;
  var HOOK_TAG = '__lining_hooked__';

  function isListApi(url) {
    return url.indexOf('list/page') >= 0 || url.indexOf('goods-jh-query/search/lining/list') >= 0;
  }

  function isDetailApi(url) {
    if (url.indexOf('list/page') >= 0) return false;
    return url.indexOf('goods-jh-query/search/lining/detail') >= 0
      || url.indexOf('/goods/detail') >= 0
      || url.indexOf('spu/detail') >= 0
      || url.indexOf('goodsInfo') >= 0
      || (url.indexOf('goods-jh-query') >= 0 && url.indexOf('detail') >= 0);
  }

  function post(type, payload) {
    try { window.postMessage({ source: 'lining-inject', type: type, data: payload }, '*'); } catch (e) {}
  }

  // 安装 hook（用原生引用，可重复调用）
  function installHooks() {
    // ---- fetch ----
    var hookedFetch = async function () {
      var a = arguments[0];
      var url = (typeof a === 'string') ? a : ((a && a.url) || '');
      var res = await NATIVE_FETCH.apply(this, arguments);
      try {
        if (isListApi(url)) {
          var data = await res.clone().json();
          post('list', { url: url, data: data });
        } else if (isDetailApi(url)) {
          var d = await res.clone().json();
          post('detail', { url: url, data: d });
          console.log('[李宁 inject] 详情API(fetch):', url.substring(0, 70));
        }
      } catch (e) {}
      return res;
    };
    hookedFetch[HOOK_TAG] = true;
    try { window.fetch = hookedFetch; } catch (e) {}

    // ---- XHR ----
    var hookedOpen = function (method, url) {
      this.__liningUrl = url;
      return NATIVE_XHR_OPEN.apply(this, arguments);
    };
    hookedOpen[HOOK_TAG] = true;

    var hookedSend = function () {
      var self = this;
      try {
        this.addEventListener('load', function () {
          try {
            var url = self.__liningUrl || '';
            if (isListApi(url)) {
              var data = JSON.parse(self.responseText);
              post('list', { url: url, data: data });
            } else if (isDetailApi(url)) {
              var d = JSON.parse(self.responseText);
              post('detail', { url: url, data: d });
              console.log('[李宁 inject] 详情API(XHR):', url.substring(0, 70));
            }
          } catch (e) {}
        });
      } catch (e) {}
      return NATIVE_XHR_SEND.apply(this, arguments);
    };
    hookedSend[HOOK_TAG] = true;

    try { XMLHttpRequest.prototype.open = hookedOpen; } catch (e) {}
    try { XMLHttpRequest.prototype.send = hookedSend; } catch (e) {}
  }

  installHooks();
  console.log('[李宁 inject] 主世界 hook 已安装');

  // 自检：每 2 秒检查 hook 是否被覆盖，被覆盖就重装
  setInterval(function () {
    try {
      var fOk = window.fetch && window.fetch[HOOK_TAG];
      var xOk = XMLHttpRequest.prototype.open && XMLHttpRequest.prototype.open[HOOK_TAG];
      if (!fOk || !xOk) {
        console.log('[李宁 inject] 检测到 hook 被覆盖，重新安装');
        installHooks();
      }
    } catch (e) {}
  }, 2000);
})();
