const STORAGE_KEY = 'agent_web_base_url'
const DEFAULT_URL = `http://${typeof window !== 'undefined' ? window.location.hostname : 'localhost'}:8000`

let baseUrl = localStorage.getItem(STORAGE_KEY) ?? DEFAULT_URL

export function getBaseUrl(): string {
  return baseUrl
}

export function setBaseUrl(url: string): void {
  baseUrl = url.replace(/\/+$/, '')
  localStorage.setItem(STORAGE_KEY, baseUrl)
}

export function getApiUrl(path: string): string {
  const base = baseUrl.replace(/\/+$/, '')
  const p = path.startsWith('/') ? path : '/' + path
  return base + p
}

export async function apiPost<T>(path: string, body?: unknown): Promise<ApiResponse<T>> {
  const url = getApiUrl(path)
  const res = await fetch(url, {
    method: 'POST',
    headers: body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
    body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  })
  const json = await res.json() as ApiResponse<T>
  return json
}

export async function apiGet<T>(path: string): Promise<ApiResponse<T>> {
  const url = getApiUrl(path)
  const res = await fetch(url)
  const json = await res.json() as ApiResponse<T>
  return json
}

/* Re-export the type for convenience */
export interface ApiResponse<T> {
  code: number
  message: string
  data?: T
}
