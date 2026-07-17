import { getBaseUrl } from '../api/client'

/**
 * 解析候选商品图片 URL。
 * - 绝对 URL（http(s)://、blob:、data:）原样返回（如李宁 CDN 真实图）。
 * - 相对路径（/api/v1/images/xxx.jpg）拼到后端 baseUrl 下，由后端静态服务。
 * 解决 candidates 携带相对路径时浏览器无法直接加载的问题。
 */
export function resolveImageUrl(url?: string | null): string {
  if (!url) return ''
  if (/^(https?:|blob:|data:)/i.test(url)) return url
  const base = getBaseUrl().replace(/\/+$/, '')
  return base + (url.startsWith('/') ? url : '/' + url)
}
