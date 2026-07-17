import { getApiUrl } from './client'
import type { SseEvent, Candidate, Citation, FinalEvent, PipelineStep } from '../types'

export function connectStream(streamUrl: string): EventSource {
  const url = getApiUrl(streamUrl)
  return new EventSource(url)
}

export function connectStreamWithParser(
  streamUrl: string,
  onEvent: (event: SseEvent) => void,
  onError: (message: string) => void,
  onClose: () => void,
): EventSource {
  const url = getApiUrl(streamUrl)
  const es = new EventSource(url)

  es.addEventListener('candidates', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data)
      const candidates: Candidate[] = data.candidates ?? []
      onEvent({ type: 'candidates', candidates })
    } catch { /* ignore parse errors */ }
  })

  es.addEventListener('delta_text', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data)
      onEvent({ type: 'delta_text', text: data.text ?? '' })
    } catch { /* ignore */ }
  })

  es.addEventListener('citations', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data)
      const citations: Citation[] = data.citations ?? []
      onEvent({ type: 'citations', citations })
    } catch { /* ignore */ }
  })

  es.addEventListener('pipeline_step', (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data)
      const step: PipelineStep = {
        step: data.step ?? '',
        label: data.label ?? '',
        status: data.status ?? 'running',
      }
      onEvent({ type: 'pipeline_step', step })
    } catch { /* ignore */ }
  })

  es.addEventListener('final', (e: MessageEvent) => {
    try {
      const finalEvent: FinalEvent = JSON.parse(e.data)
      onEvent({ type: 'final', event: finalEvent })
    } catch { /* ignore */ }
  })

  // 业务错误事件（后端主动 emit 的 event:error，e.data 是 JSON）
  // 注意：连接层断开时也会触发 error 但 e.data 为 undefined，交给 onerror
  es.addEventListener('error', (e: MessageEvent) => {
    if (!e.data) return
    try {
      const data = JSON.parse(e.data)
      onEvent({ type: 'error', message: data.message ?? '处理出错' })
    } catch { /* ignore */ }
  })

  es.onerror = () => {
    onError('SSE 连接断开')
  }

  return es
}
