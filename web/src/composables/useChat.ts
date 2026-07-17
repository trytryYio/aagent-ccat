import { reactive, ref, onUnmounted } from 'vue'
import type { ChatUiState, ChatMessage, ConnectionStatus, SseEvent, FinalEvent, PipelineStep } from '../types'
import { uploadImage, createChat, stopChat, healthCheck } from '../api/chat'
import { connectStreamWithParser } from '../api/sse'
import { getApiUrl } from '../api/client'

let messageCounter = 0

export function useChat() {
  const state = reactive<ChatUiState>({
    messages: [],
    isStreaming: false,
    currentStreamingMessageId: null,
    isUploading: false,
    uploadProgress: 0,
    error: null,
    connectionStatus: 'checking',
    selectedImageFile: null,
    selectedImagePreviewUrl: null,
  })

  let sessionId: string | null = sessionStorage.getItem('chat_session_id') || null
  let currentImageId: string | null = null
  let eventSource: EventSource | null = null

  // ---------- Load History ----------

  const loadHistory = async () => {
    if (!sessionId) return
    try {
      const url = getApiUrl(`/api/v1/chat/history?session_id=${sessionId}`)
      const res = await fetch(url)
      const json = await res.json()
      if (json.code === 0 && json.data?.history?.length > 0) {
        const msgs: ChatMessage[] = json.data.history.map((h: { role: string; content: string; is_clarify?: boolean; candidates?: any[]; citations?: any[] }, i: number) => {
          // 归一化候选字段：后端用 sku，前端 types 用 sku_id
          const normalizedCandidates = h.candidates?.length
            ? h.candidates.map((c: any) => ({ ...c, sku_id: c.sku_id || c.sku }))
            : null
          return {
            id: `hist_${i}`,
            role: h.role as 'user' | 'assistant',
            text: h.content,
            candidates: normalizedCandidates,
            citations: h.citations && h.citations.length > 0 ? h.citations : null,
            needClarify: h.is_clarify ?? false,
            clarifyQuestion: h.is_clarify ? h.content : undefined,
            isLoading: false,
            isStreaming: false,
            isError: false,
          }
        })
        messageCounter = msgs.length
        state.messages = msgs
        console.log(`[History] Loaded ${msgs.length} messages for session ${sessionId}`)
      }
    } catch (e) {
      console.warn('[History] Load failed:', e)
    }
  }

  // 初始化时加载历史
  loadHistory()

  // ---------- Health Check ----------

  const checkHealth = async () => {
    state.connectionStatus = 'checking'
    try {
      const res = await healthCheck()
      state.connectionStatus = res.code === 0 ? 'connected' : 'disconnected'
    } catch {
      state.connectionStatus = 'disconnected'
    }
  }

  // Auto health check on init, then every 30s
  checkHealth()
  const healthInterval = setInterval(checkHealth, 30_000)
  onUnmounted(() => clearInterval(healthInterval))

  // ---------- Image Selection ----------

  const selectImage = (file: File | null) => {
    if (state.selectedImagePreviewUrl) {
      URL.revokeObjectURL(state.selectedImagePreviewUrl)
    }
    state.selectedImageFile = file
    state.selectedImagePreviewUrl = file ? URL.createObjectURL(file) : null
  }

  const clearSelectedImage = () => {
    if (state.selectedImagePreviewUrl) {
      URL.revokeObjectURL(state.selectedImagePreviewUrl)
    }
    state.selectedImageFile = null
    state.selectedImagePreviewUrl = null
  }

  // ---------- Send Message ----------

  const sendMessage = async (text?: string | null) => {
    const file = state.selectedImageFile

    if (!file) {
      // 纯文字检索（无需图片，后端支持 session_id + text）
      if (!text || text.trim() === '') return
      addUserMessage(text ?? '')
      startChat(text ?? '')
      return
    }

    state.isUploading = true
    state.uploadProgress = 0

    try {
      // Validate file
      const maxSize = 10 * 1024 * 1024 // 10MB
      if (file.size > maxSize) {
        state.error = '图片大小超过 10MB 限制'
        state.isUploading = false
        return
      }
      const validTypes = ['image/jpeg', 'image/png', 'image/webp']
      if (!validTypes.includes(file.type)) {
        state.error = '仅支持 jpg/png/webp 格式'
        state.isUploading = false
        return
      }

      // Upload
      const uploadRes = await uploadImage(file)
      if (uploadRes.code !== 0 || !uploadRes.data) {
        state.error = uploadRes.message || '图片上传失败，请重试'
        state.isUploading = false
        state.connectionStatus = 'disconnected'
        return
      }

      currentImageId = uploadRes.data.image_id
      addUserMessage(text ?? '', file.name)

      state.isUploading = false
      state.selectedImageFile = null
      state.selectedImagePreviewUrl = null
      state.connectionStatus = 'connected'

      startChat(text ?? '')
    } catch {
      state.error = '处理图片失败，请重试'
      state.isUploading = false
    }
  }

  // ---------- Stop Generation ----------

  const stopGeneration = () => {
    eventSource?.close()
    eventSource = null

    const streamingId = state.currentStreamingMessageId
    const msgs = state.messages
    const streamingIdx = msgs.findIndex(m => m.id === streamingId)

    if (streamingIdx >= 1) {
      const newMsgs = [...msgs]
      newMsgs.splice(streamingIdx, 1) // remove AI message
      if (newMsgs[streamingIdx - 1]?.role === 'user') {
        newMsgs.splice(streamingIdx - 1, 1) // remove user message
      }
      state.messages = newMsgs
    } else if (streamingIdx >= 0) {
      state.messages = msgs.filter(m => m.id !== streamingId)
    }

    state.isStreaming = false
    state.currentStreamingMessageId = null

    if (streamingId) {
      stopChat(streamingId).catch(() => {})
    }
  }

  // ---------- Clarification ----------

  const sendClarification = (text: string) => {
    if (!text.trim()) return
    addUserMessage(text)
    startChat(text)
  }

  // ---------- Clear Error ----------

  const clearError = () => {
    state.error = null
  }

  // ---------- Retry ----------

  const retryLastMessage = () => {
    state.error = null
    sendMessage()
  }

  // ---------- New Session ----------

  const newSession = () => {
    eventSource?.close()
    eventSource = null
    sessionId = null
    sessionStorage.removeItem('chat_session_id')
    currentImageId = null
    messageCounter = 0
    state.messages = []
    state.isStreaming = false
    state.currentStreamingMessageId = null
    state.error = null
  }

  // ---------- Private Helpers ----------

  const addUserMessage = (text: string, imageUrl?: string | null) => {
    const msg: ChatMessage = {
      id: `msg_${++messageCounter}`,
      role: 'user',
      text,
      imageUrl: imageUrl ?? null,
      imageLocalUri: imageUrl ?? null,
      candidates: null,
      citations: null,
      needClarify: false,
      clarifyQuestion: null,
      isLoading: false,
      isStreaming: false,
      isError: false,
      errorMessage: null,
    }
    state.messages = [...state.messages, msg]
  }

  const startChat = async (text?: string) => {
    const msgId = `msg_${++messageCounter}`
    const assistantMsg: ChatMessage = {
      id: msgId,
      role: 'assistant',
      text: '',
      candidates: null,
      citations: null,
      pipelineSteps: [],    // 初始化管线进度
      needClarify: false,
      clarifyQuestion: null,
      isLoading: true,
      isStreaming: false,
      isError: false,
      errorMessage: null,
    }

    state.messages = [...state.messages, assistantMsg]
    state.isStreaming = true
    state.currentStreamingMessageId = msgId
    state.error = null

    try {
      // 把当前消息列表（去掉最后一条 assistant placeholder）作为历史传给后端
      const history = state.messages
        .filter(m => !m.isLoading) // 排除加载中的占位消息
        .slice(0, -1) // 去掉刚加的 assistantMsg
        .map(m => ({ role: m.role, text: m.text }))

      const chatRes = await createChat(sessionId, currentImageId ?? '', text, history)
      if (chatRes.code !== 0 || !chatRes.data) {
        updateStreamingError(chatRes.message || '请求失败')
        return
      }

      const info = chatRes.data
      sessionId = info.session_id
      sessionStorage.setItem('chat_session_id', info.session_id)

      eventSource = connectStreamWithParser(
        info.stream_url,
        (event: SseEvent) => handleSseEvent(event, msgId),
        (errMsg: string) => {
          const friendly = friendlyError(errMsg)
          if (friendly !== '已停止生成') {
            updateStreamingError(friendly)
            state.connectionStatus = 'disconnected'
          }
        },
        () => {
          // on close — nothing needed here since we handle final event
        },
      )
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : ''
      updateStreamingError(friendlyError(msg))
    }
  }

  const handleSseEvent = (event: SseEvent, targetMsgId: string) => {
    const idx = state.messages.findIndex(m => m.id === targetMsgId || m.isStreaming)
    if (idx < 0) return
    const msg = state.messages[idx]

    switch (event.type) {
      case 'candidates': {
        // 归一化字段：后端用 sku 前端用 sku_id
        const normalized = event.candidates.map((c: any) => ({ ...c, sku_id: c.sku_id || c.sku }))
        const updated = { ...msg, candidates: normalized }
        const msgs = [...state.messages]
        msgs[idx] = updated
        state.messages = msgs
        break
      }
      case 'delta_text': {
        // 第一个 token 到来：切换为流式模式
        const isFirstToken = !msg.isStreaming && !msg.text
        const updated = {
          ...msg,
          text: msg.text + event.text,
          isLoading: false,
          isStreaming: true,
        }
        const msgs = [...state.messages]
        msgs[idx] = updated
        state.messages = msgs
        break
      }
      case 'citations': {
        const updated = { ...msg, citations: event.citations }
        const msgs = [...state.messages]
        msgs[idx] = updated
        state.messages = msgs
        break
      }
      case 'pipeline_step': {
        const step = event.step
        const existingSteps = msg.pipelineSteps ?? []
        // 查找是否已存在该步骤
        const stepIdx = existingSteps.findIndex(s => s.step === step.step)
        let newSteps: PipelineStep[]
        if (stepIdx >= 0) {
          // 更新已有步骤的状态
          newSteps = [...existingSteps]
          newSteps[stepIdx] = step
        } else {
          // 添加新步骤
          newSteps = [...existingSteps, step]
        }
        const updated = { ...msg, pipelineSteps: newSteps }
        const msgs = [...state.messages]
        msgs[idx] = updated
        state.messages = msgs
        break
      }
      case 'final': {
        const fe = event.event
        const updated = {
          ...msg,
          isStreaming: false,
          isLoading: false,
          needClarify: fe.need_clarify,
          clarifyQuestion: fe.clarify_question ?? null,
        }
        const msgs = [...state.messages]
        msgs[idx] = updated
        state.messages = msgs
        state.isStreaming = false
        state.currentStreamingMessageId = null
        eventSource?.close()
        eventSource = null
        break
      }
      case 'error': {
        updateStreamingError(event.message)
        break
      }
    }
  }

  const updateStreamingError = (errMsg: string) => {
    const id = state.currentStreamingMessageId
    if (id) {
      const idx = state.messages.findIndex(m => m.id === id)
      if (idx >= 0) {
        const msgs = [...state.messages]
        msgs[idx] = {
          ...msgs[idx],
          isStreaming: false,
          isLoading: false,
          isError: true,
          errorMessage: errMsg,
        }
        state.messages = msgs
      }
    }
    state.isStreaming = false
    state.currentStreamingMessageId = null
    state.error = errMsg
    eventSource?.close()
    eventSource = null
  }

  const friendlyError = (msg: string): string => {
    if (msg.includes('timeout')) return '网络连接超时，请检查后端服务'
    if (msg.includes('refused') || msg.includes('Failed to connect') || msg.includes('Unable to resolve host')) {
      return '无法连接到服务器，请确认后端已启动'
    }
    if (msg.includes('Cancel')) return '已停止生成'
    if (msg.includes('SSE')) return '网络连接已断开，请检查后端服务'
    return msg || '服务器出错了，请稍后重试'
  }

  return {
    state,
    sendMessage,
    stopGeneration,
    sendClarification,
    clearError,
    retryLastMessage,
    newSession,
    checkHealth,
    selectImage,
    clearSelectedImage,
  }
}
