import { apiPost, apiGet, getApiUrl } from './client'
import type { ApiResponse, UploadData, ChatData } from '../types'

export async function uploadImage(file: File): Promise<ApiResponse<UploadData>> {
  const form = new FormData()
  form.append('file', file)
  return apiPost<UploadData>('/api/v1/upload/image', form)
}

export async function createChat(
  sessionId: string | null,
  imageId: string,
  text?: string | null,
  history?: { role: string; text: string }[] | null,
): Promise<ApiResponse<ChatData>> {
  return apiPost<ChatData>('/api/v1/chat', {
    session_id: sessionId,
    image_id: imageId,
    text: text ?? null,
    history: history ?? [],
  })
}

export async function stopChat(messageId: string): Promise<ApiResponse<undefined>> {
  return apiPost<undefined>('/api/v1/chat/stop', { message_id: messageId })
}

export async function healthCheck(): Promise<ApiResponse<undefined>> {
  return apiGet<undefined>('/api/v1/health')
}
