/** 知识库管理 API 客户端 */

import { getApiUrl } from './client'
import type { ApiResponse } from '../types'

export interface UploadedFile {
  original_name: string
  saved_as: string
  size: number
  path: string
}

export interface UploadResult {
  saved: UploadedFile[]
  errors: { filename: string; error: string }[]
}

export interface DocumentInfo {
  doc_id: string
  file_name: string
  source: string
  chunk_count: number
}

export interface DocumentListResult {
  documents: DocumentInfo[]
  total: number
}

export interface IngestResult {
  count: number
}

export async function uploadDocs(files: File[]): Promise<UploadResult> {
  const form = new FormData()
  for (const f of files) form.append('files', f)
  const url = getApiUrl('/api/v1/admin/docs/upload')
  const res = await fetch(url, { method: 'POST', body: form })
  const json: ApiResponse<UploadResult> = await res.json()
  if (json.code !== 0 || !json.data) {
    throw new Error(json.message || '上传失败')
  }
  return json.data
}

export async function triggerIngest(files?: string[]): Promise<{ count: number }> {
  const url = getApiUrl('/api/v1/admin/docs/ingest')
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ files: files || null }),
  })
  const json: ApiResponse<IngestResult> = await res.json()
  if (json.code !== 0 || !json.data) {
    throw new Error(json.message || '入库失败')
  }
  return json.data
}

export async function listDocuments(): Promise<DocumentListResult> {
  const url = getApiUrl('/api/v1/admin/docs/list')
  const res = await fetch(url)
  const json: ApiResponse<DocumentListResult> = await res.json()
  if (json.code !== 0 || !json.data) {
    throw new Error(json.message || '获取列表失败')
  }
  return json.data
}

export async function deleteDocument(docId: string): Promise<void> {
  const url = getApiUrl(`/api/v1/admin/docs/${encodeURIComponent(docId)}`)
  const res = await fetch(url, { method: 'DELETE' })
  const json: ApiResponse<unknown> = await res.json()
  if (json.code !== 0) {
    throw new Error(json.message || '删除失败')
  }
}

export async function getIngestStatus(): Promise<{ status: string; in_progress: number }> {
  const url = getApiUrl('/api/v1/admin/docs/status')
  const res = await fetch(url)
  const json: ApiResponse<{ status: string; in_progress: number; last_run: string | null }> = await res.json()
  if (json.code !== 0 || !json.data) {
    throw new Error(json.message || '获取状态失败')
  }
  return json.data
}
