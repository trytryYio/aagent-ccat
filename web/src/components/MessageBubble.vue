<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import type { ChatMessage } from '../types'
import CandidateCard from './CandidateCard.vue'
import CitationSection from './CitationSection.vue'
import ClarifySection from './ClarifySection.vue'
import PipelineProgress from './PipelineProgress.vue'
import SkeletonCard from './SkeletonCard.vue'
import { useCardStagger } from '../composables/useGSAP'

const props = defineProps<{ message: ChatMessage }>()
const emit = defineEmits<{ clarifySend: [text: string] }>()
const candidatesRef = ref<HTMLElement | null>(null)
const { animateCards } = useCardStagger()

// 轻量级 Markdown 渲染
function renderMarkdown(text: string): string {
  if (!text) return ''
  let html = text
  // 先转义 HTML
  html = html.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

  // === 表格（必须在其他行级规则之前处理，因为表格行包含 | 字符）===
  html = html.replace(
    /(?:^|\n)((?:\|.+\|\n)+)/g,
    (_match, tableBlock: string) => {
      const rows = tableBlock.trim().split('\n')
      if (rows.length < 2) return tableBlock  // 至少需要表头+分隔行

      // 检查第二行是否是分隔行（|---|---|）
      const sepRow = rows[1]
      if (!/^\|[\s\-:|]+\|$/.test(sepRow)) return tableBlock

      // 解析表头
      const headers = rows[0].split('|').filter(c => c.trim() !== '').map(c => c.trim())

      // 解析数据行（跳过表头和分隔行）
      const dataRows = rows.slice(2).map(row =>
        row.split('|').filter(c => c.trim() !== '').map(c => c.trim())
      )

      // 生成 HTML 表格
      let table = '\n<div class="my-2 overflow-x-auto"><table class="w-full text-xs border-collapse border border-outline/20 rounded">'
      // 表头
      table += '<thead><tr class="bg-primary/5">'
      for (const h of headers) {
        table += `<th class="border border-outline/20 px-2 py-1.5 text-left font-semibold">${h}</th>`
      }
      table += '</tr></thead>'
      // 数据行
      table += '<tbody>'
      for (const row of dataRows) {
        table += '<tr class="hover:bg-primary/3">'
        for (let i = 0; i < headers.length; i++) {
          const cell = row[i] || ''
          table += `<td class="border border-outline/20 px-2 py-1.5">${cell}</td>`
        }
        table += '</tr>'
      }
      table += '</tbody></table></div>\n'
      return table
    },
  )

  // 标题 ### text
  html = html.replace(/^### (.+)$/gm, '<h4 class="text-sm font-semibold mt-3 mb-1">$1</h4>')
  html = html.replace(/^## (.+)$/gm, '<h3 class="text-base font-semibold mt-3 mb-1">$1</h3>')
  html = html.replace(/^# (.+)$/gm, '<h2 class="text-lg font-bold mt-3 mb-1">$1</h2>')

  // 分隔线 ---（排除表格分隔行已处理的）
  html = html.replace(/^---$/gm, '<hr class="my-2 border-outline/20">')

  // 无序列表 - item
  html = html.replace(/^- (.+)$/gm, '<li class="ml-4 text-sm">$1</li>')
  // 有序列表 1. item
  html = html.replace(/^\d+\. (.+)$/gm, '<li class="ml-4 text-sm">$1</li>')

  // 粗体 **text**
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
  // 斜体 *text*
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')
  // 行内代码 `code`
  html = html.replace(/`(.+?)`/g, '<code class="bg-black/5 px-1 rounded text-xs">$1</code>')
  // 链接 [text](url)
  html = html.replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank" class="text-primary underline">$1</a>')

  // 块引用 > text
  html = html.replace(/^> (.+)$/gm, '<blockquote class="border-l-2 border-primary/30 pl-3 text-xs text-on-surface-variant my-1">$1</blockquote>')

  // 换行符：双换行 → 段落，单换行 → <br>
  html = html.replace(/\n\n/g, '</p><p class="text-sm leading-relaxed">')
  html = html.replace(/\n/g, '<br>')

  // 包裹在段落中
  html = '<p class="text-sm leading-relaxed">' + html + '</p>'

  return html
}

const renderedMarkdown = computed(() => {
  return renderMarkdown(props.message.text)
})

// 候选商品出现时交错入场
watch(
  () => props.message.candidates?.length ?? 0,
  async (n) => {
    if (n > 0) {
      await nextTick()
      const els = candidatesRef.value?.querySelectorAll<HTMLElement>('.candidate-card')
      if (els && els.length) animateCards(Array.from(els))
    }
  },
)
</script>

<template>
  <div class="message-bubble flex w-full" :class="message.role === 'user' ? 'justify-end' : 'justify-start'">
    <div :class="message.role === 'user' ? 'items-end' : 'items-start'" class="flex flex-col max-w-[75%] sm:max-w-[70%]">
      <!-- User image -->
      <img
        v-if="message.imageLocalUri && message.role === 'user'"
        :src="message.imageLocalUri"
        alt="用户图片"
        class="w-28 h-28 object-cover rounded-xl mb-1.5"
      />

      <div class="flex gap-2.5" :class="message.role === 'assistant' ? '' : ''">
        <!-- Bot avatar — 李宁 Logo（品牌红 #E60012） -->
        <div
          v-if="message.role === 'assistant'"
          class="shrink-0 w-8 h-8 rounded-full flex items-center justify-center bg-white border border-outline/10"
        >
          <svg class="w-5 h-5" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
            <!-- 李宁标志的简化版：左侧斜 L + 右侧反斜 L -->
            <path
              d="M5 4 L5 26 L12 26 L12 11 L16 14 L16 26 L23 26 L23 11 L27 8 L27 4 Z"
              fill="#E60012"
              fill-rule="evenodd"
            />
          </svg>
        </div>

        <!-- Bubble content -->
        <div
          class="rounded-2xl px-3.5 py-3"
          :class="message.role === 'user' ? 'rounded-br-sm' : 'rounded-bl-sm'"
          :style="{
            backgroundColor: message.role === 'user' ? 'rgba(44, 44, 46, 0.08)' : 'var(--color-surface)',
            boxShadow: message.role === 'user' ? 'none' : '0 1px 3px rgba(0,0,0,0.08)',
          }"
        >
          <!-- Pipeline Progress（管线进度提示，优先显示） -->
          <PipelineProgress
            v-if="message.pipelineSteps && message.pipelineSteps.length > 0 && !message.isStreaming"
            :steps="message.pipelineSteps"
          />

          <!-- Thinking indicator（无进度时显示基础加载动画） -->
          <div
            v-if="message.isLoading && !message.isStreaming && (!message.pipelineSteps || message.pipelineSteps.length === 0)"
            class="flex items-center gap-2 py-1"
          >
            <div class="flex gap-1">
              <span class="w-1.5 h-1.5 rounded-full bg-primary/60 animate-pulse" style="animation-delay: 0ms" />
              <span class="w-1.5 h-1.5 rounded-full bg-primary/60 animate-pulse" style="animation-delay: 200ms" />
              <span class="w-1.5 h-1.5 rounded-full bg-primary/60 animate-pulse" style="animation-delay: 400ms" />
            </div>
            <span class="text-xs text-on-surface-variant/60">AI 正在分析中...</span>
          </div>

          <!-- Text (文字推荐在上面) -->
          <div
            v-if="message.text && !(message.isLoading && !message.isStreaming)"
            class="text-sm leading-relaxed text-on-surface whitespace-pre-wrap break-words markdown-body"
            v-html="renderedMarkdown"
          />

          <!-- Candidates (商品卡片在文字下方) -->
          <div ref="candidatesRef" v-if="message.candidates && message.candidates.length > 0" class="mt-2.5">
            <p class="text-xs font-medium text-on-surface-variant/70 mb-2">候选商品</p>
            <div class="flex gap-2.5 overflow-x-auto scrollbar-hide">
              <CandidateCard
                v-for="c in message.candidates"
                :key="c.sku_id"
                :candidate="c"
              />
            </div>
          </div>

          <!-- Citations -->
          <CitationSection
            v-if="message.citations && message.citations.length > 0"
            :citations="message.citations"
          />

          <!-- Error -->
          <div v-if="message.isError" class="flex items-center gap-1 mt-1">
            <svg class="w-3.5 h-3.5 shrink-0" style="color: #BA3B3B" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M18.364 5.636a9 9 0 11-12.728 0 9 9 0 0112.728 0zM12 9v4m0 4h.01" />
            </svg>
            <span class="text-xs" style="color: #BA3B3B">{{ message.errorMessage || '出错了' }}</span>
          </div>
        </div>
      </div>

      <!-- Clarify -->
      <ClarifySection
        v-if="message.needClarify && message.clarifyQuestion"
        :question="message.clarifyQuestion"
        @send="emit('clarifySend', $event)"
      />
    </div>
  </div>
</template>
