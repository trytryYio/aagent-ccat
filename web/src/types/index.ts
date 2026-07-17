/* -------- API Response Wrapper -------- */

export interface ApiResponse<T> {
  code: number
  message: string
  data?: T
}

/* -------- Upload -------- */

export interface UploadData {
  image_id: string
  url: string
  width: number
  height: number
  size: number
}

/* -------- Chat -------- */

export interface ChatRequest {
  session_id: string | null
  image_id: string
  text?: string | null
}

export interface ChatData {
  session_id: string
  message_id: string
  stream_url: string
}

export interface StopRequest {
  message_id: string
}

/* -------- Core Business Models -------- */

export interface Candidate {
  sku_id: string
  score: number
  title: string
  image_url: string
  attrs?: Record<string, string>
  price?: number
  detail_images?: string  // 商品详情图片 URL 列表（逗号分隔或 JSON 数组）
}

export interface Citation {
  sku_id: string
  chunk_id: string
  snippet: string
  source: string
}

/* -------- Pipeline Progress -------- */

export interface PipelineStep {
  step: string       // 节点名（如 "intent_recognition"）
  label: string      // 中文标签（如 "识别意图"）
  status: 'running' | 'done' | 'error'
}

/* -------- SSE Events -------- */

export interface FinalEvent {
  need_clarify: boolean
  clarify_question?: string
  usage?: {
    total_tokens?: number
    prompt_tokens?: number
    completion_tokens?: number
  }
}

export type SseEvent =
  | { type: 'candidates'; candidates: Candidate[] }
  | { type: 'delta_text'; text: string }
  | { type: 'citations'; citations: Citation[] }
  | { type: 'pipeline_step'; step: PipelineStep }
  | { type: 'final'; event: FinalEvent }
  | { type: 'error'; message: string }

/* -------- UI Chat Message -------- */

export type MessageRole = 'user' | 'assistant'
export type ConnectionStatus = 'connected' | 'disconnected' | 'checking'

export interface ChatMessage {
  id: string
  role: MessageRole
  text: string
  imageUrl?: string | null
  imageLocalUri?: string | null
  candidates?: Candidate[] | null
  citations?: Citation[] | null
  pipelineSteps?: PipelineStep[] | null   // 管线进度步骤
  needClarify: boolean
  clarifyQuestion?: string | null
  isLoading: boolean
  isStreaming: boolean
  isError: boolean
  errorMessage?: string | null
}

/* -------- Chat UI State -------- */

export interface ChatUiState {
  messages: ChatMessage[]
  isStreaming: boolean
  currentStreamingMessageId: string | null
  isUploading: boolean
  uploadProgress: number
  error: string | null
  connectionStatus: ConnectionStatus
  selectedImageFile: File | null
  selectedImagePreviewUrl: string | null
}
