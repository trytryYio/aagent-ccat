<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  uploadDocs,
  triggerIngest,
  listDocuments,
  deleteDocument,
  type UploadedFile,
  type DocumentInfo,
} from '../api/knowledge'

const router = useRouter()

// === 状态 ===
const selectedFiles = ref<File[]>([])
const uploadedResults = ref<UploadedFile[]>([])
const uploadErrors = ref<{ filename: string; error: string }[]>([])
const documents = ref<DocumentInfo[]>([])
const isUploading = ref(false)
const isIngesting = ref(false)
const statusMessage = ref('')
const isDragging = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const SUPPORTED_EXTS = ['.pdf', '.md', '.txt', '.csv', '.xlsx', '.docx']
const SUPPORTED_FORMATS = SUPPORTED_EXTS.join(', ')

// === 派生数据 ===
const totalSize = computed(() => selectedFiles.value.reduce((sum, f) => sum + f.size, 0))
const totalSizeText = computed(() => {
  const b = totalSize.value
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / 1024 / 1024).toFixed(2)} MB`
})

// === 事件处理 ===
function openFilePicker() {
  fileInput.value?.click()
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files) return
  addFiles(Array.from(input.files))
  input.value = ''
}

function addFiles(files: File[]) {
  for (const f of files) {
    const ext = '.' + (f.name.split('.').pop() || '').toLowerCase()
    if (!SUPPORTED_EXTS.includes(ext)) {
      statusMessage.value = `不支持的格式: ${f.name}`
      setTimeout(() => (statusMessage.value = ''), 3000)
      continue
    }
    selectedFiles.value.push(f)
  }
}

function removeFile(idx: number) {
  selectedFiles.value.splice(idx, 1)
}

function clearFiles() {
  selectedFiles.value = []
}

async function handleUpload() {
  if (selectedFiles.value.length === 0) return
  isUploading.value = true
  statusMessage.value = ''
  try {
    const result = await uploadDocs(selectedFiles.value)
    uploadedResults.value = result.saved
    uploadErrors.value = result.errors
    statusMessage.value = `成功上传 ${result.saved.length} 个文件${result.errors.length > 0 ? `，${result.errors.length} 个失败` : ''}`
    // 上传成功后清空选择列表
    selectedFiles.value = []
    // 自动触发入库
    if (result.saved.length > 0) {
      await handleIngest(result.saved.map(f => f.saved_as))
    }
    // 刷新文档列表
    await loadDocuments()
  } catch (e) {
    statusMessage.value = `上传失败: ${e instanceof Error ? e.message : String(e)}`
  } finally {
    isUploading.value = false
  }
}

async function handleIngest(files?: string[]) {
  isIngesting.value = true
  statusMessage.value = '正在入库...'
  try {
    await triggerIngest(files)
    statusMessage.value = '入库任务已启动（异步执行，1-2 分钟后刷新列表）'
    // 延迟 3 秒后自动刷新一次（给后台任务启动时间）
    setTimeout(() => loadDocuments(), 3000)
  } catch (e) {
    statusMessage.value = `入库失败: ${e instanceof Error ? e.message : String(e)}`
  } finally {
    isIngesting.value = false
  }
}

async function loadDocuments() {
  try {
    const result = await listDocuments()
    documents.value = result.documents
  } catch (e) {
    statusMessage.value = `加载列表失败: ${e instanceof Error ? e.message : String(e)}`
  }
}

async function handleDelete(doc: DocumentInfo) {
  if (!confirm(`确认删除文档「${doc.file_name}」及其 ${doc.chunk_count} 个 chunks？`)) return
  try {
    await deleteDocument(doc.doc_id)
    statusMessage.value = `已删除 ${doc.file_name}`
    await loadDocuments()
  } catch (e) {
    statusMessage.value = `删除失败: ${e instanceof Error ? e.message : String(e)}`
  }
}

// 拖拽上传
function onDragOver(e: DragEvent) {
  e.preventDefault()
  isDragging.value = true
}
function onDragLeave() {
  isDragging.value = false
}
function onDrop(e: DragEvent) {
  e.preventDefault()
  isDragging.value = false
  const files = e.dataTransfer?.files
  if (files && files.length > 0) {
    addFiles(Array.from(files))
  }
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`
  return `${(b / 1024 / 1024).toFixed(2)} MB`
}

onMounted(() => {
  loadDocuments()
})
</script>

<template>
  <div class="h-full flex flex-col bg-bg">
    <!-- Top bar -->
    <header class="flex items-center gap-2 px-4 h-14 bg-bg shrink-0 border-b border-outline/30">
      <button
        class="p-2 rounded-lg text-on-surface/60 hover:bg-surface-variant transition-colors"
        title="返回"
        @click="router.back()"
      >
        <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
        </svg>
      </button>
      <h1 class="text-lg font-semibold text-on-surface">RAG 知识库</h1>
    </header>

    <!-- 主内容滚动区 -->
    <div class="flex-1 overflow-y-auto p-4 space-y-4">
      <!-- Status 消息 -->
      <div
        v-if="statusMessage"
        class="rounded-lg px-3 py-2 text-xs"
        :class="statusMessage.includes('失败') ? 'bg-error-container text-on-error-container' : 'bg-surface-variant text-on-surface'"
      >
        {{ statusMessage }}
      </div>

      <!-- 上传区 -->
      <section class="bg-surface rounded-2xl p-4 shadow-sm">
        <h2 class="text-sm font-semibold text-on-surface mb-3">上传文档</h2>

        <!-- 拖拽区 -->
        <div
          class="border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors"
          :class="isDragging ? 'border-primary bg-primary-container/30' : 'border-outline hover:border-on-surface-variant'"
          @click="openFilePicker"
          @dragover="onDragOver"
          @dragleave="onDragLeave"
          @drop="onDrop"
        >
          <svg class="w-8 h-8 mx-auto mb-2 text-on-surface-variant" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
          </svg>
          <p class="text-sm text-on-surface mb-1">点击或拖拽文件到此处</p>
          <p class="text-xs text-on-surface-variant">
            支持 {{ SUPPORTED_FORMATS }}（最大 50MB/文件）
          </p>
        </div>

        <!-- 隐藏的 file input -->
        <input
          ref="fileInput"
          type="file"
          multiple
          :accept="SUPPORTED_EXTS.join(',')"
          class="hidden"
          @change="onFileChange"
        />

        <!-- 已选文件列表 -->
        <div v-if="selectedFiles.length > 0" class="mt-3 space-y-1.5">
          <div class="flex items-center justify-between mb-1.5">
            <span class="text-xs text-on-surface-variant">
              已选择 {{ selectedFiles.length }} 个文件，共 {{ totalSizeText }}
            </span>
            <button
              class="text-xs text-on-surface-variant hover:text-on-surface transition-colors"
              @click="clearFiles"
            >
              清空
            </button>
          </div>
          <div
            v-for="(file, idx) in selectedFiles"
            :key="idx"
            class="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-variant"
          >
            <svg class="w-4 h-4 shrink-0 text-on-surface-variant" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
              <path stroke-linecap="round" stroke-linejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
            </svg>
            <div class="flex-1 min-w-0">
              <p class="text-xs text-on-surface truncate">{{ file.name }}</p>
              <p class="text-[10px] text-on-surface-variant">{{ formatBytes(file.size) }}</p>
            </div>
            <button
              class="p-1 rounded text-on-surface-variant hover:text-error transition-colors"
              @click="removeFile(idx)"
            >
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <button
            class="w-full mt-2 py-2.5 rounded-lg text-sm font-medium bg-primary text-on-primary hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
            :disabled="isUploading || selectedFiles.length === 0"
            @click="handleUpload"
          >
            {{ isUploading ? '上传中…' : `上传 ${selectedFiles.length} 个文件` }}
          </button>
        </div>
      </section>

      <!-- 已入库文档 -->
      <section class="bg-surface rounded-2xl p-4 shadow-sm">
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-semibold text-on-surface">已入库文档</h2>
          <div class="flex items-center gap-2">
            <span class="text-xs text-on-surface-variant">
              {{ documents.length }} 个文档 / {{ documents.reduce((s, d) => s + d.chunk_count, 0) }} 个 chunks
            </span>
            <button
              class="p-1.5 rounded-lg text-on-surface-variant hover:bg-surface-variant transition-colors"
              title="刷新"
              @click="loadDocuments"
            >
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M16.023 9.348h4.992v-.001M2.985h19.023v-.001M2.985 19.023v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
              </svg>
            </button>
          </div>
        </div>

        <!-- 文档列表 -->
        <div v-if="documents.length === 0" class="text-center py-8">
          <p class="text-xs text-on-surface-variant">暂无文档，先上传并入库</p>
        </div>

        <div v-else class="space-y-1.5">
          <div
            v-for="doc in documents"
            :key="doc.doc_id"
            class="flex items-center gap-2 px-3 py-2.5 rounded-lg bg-surface-variant"
          >
            <div class="flex-1 min-w-0">
              <p class="text-xs font-medium text-on-surface truncate">
                {{ doc.file_name || doc.source.split('/').pop() || 'unnamed' }}
              </p>
              <p class="text-[10px] text-on-surface-variant font-mono mt-0.5 truncate">
                {{ doc.doc_id }} · {{ doc.chunk_count }} chunks
              </p>
            </div>
            <button
              class="p-1.5 rounded text-on-surface-variant hover:text-error transition-colors"
              title="删除"
              @click="handleDelete(doc)"
            >
              <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                <path stroke-linecap="round" stroke-linejoin="round" d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
              </svg>
            </button>
          </div>
        </div>

        <!-- 重新入库全部 -->
        <button
          v-if="documents.length > 0"
          class="w-full mt-3 py-2 rounded-lg text-xs font-medium text-on-surface border border-outline hover:bg-surface-variant transition-colors"
          :disabled="isIngesting"
          @click="handleIngest()"
        >
          {{ isIngesting ? '入库中…' : '重新入库 docs/ 目录全部文档' }}
        </button>
      </section>

      <p class="text-[10px] text-on-surface-variant/60 text-center pt-2">
        文档解析：自动检测格式（PDF/MD/DOCX/XLSX/TXT/CSV）
      </p>
    </div>
  </div>
</template>
