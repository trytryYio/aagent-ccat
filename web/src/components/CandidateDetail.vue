<script setup lang="ts">
import { computed } from 'vue'
import type { Candidate } from '../types'
import { resolveImageUrl } from '../utils/image'

const props = defineProps<{ candidate: Candidate }>()
const emit = defineEmits<{ dismiss: [] }>()

function onBackdropClick(e: MouseEvent) {
  if ((e.target as HTMLElement).classList.contains('dialog-backdrop')) {
    emit('dismiss')
  }
}

function scorePercent(score: number): number {
  return Math.round(score * 100)
}

// 解析 detail_images: 支持逗号或管道分隔的 URL，也支持 JSON 数组
const detailImageUrls = computed(() => {
  const raw = props.candidate.detail_images
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) return parsed.filter(Boolean)
  } catch { /* not JSON */ }
  return raw.split(/[,\|]/).map(s => s.trim()).filter(Boolean)
})
</script>

<template>
  <div
    class="dialog-backdrop fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    @click="onBackdropClick"
  >
    <!-- 左右两栏布局 -->
    <div class="bg-surface rounded-2xl w-full max-w-4xl max-h-[90vh] shadow-2xl flex flex-col sm:flex-row overflow-hidden">

      <!-- 左侧：主信息 -->
      <div class="sm:w-1/2 p-5 overflow-y-auto max-h-[90vh] border-r border-outline/10">
        <img
          :src="resolveImageUrl(candidate.image_url)"
          :alt="candidate.title"
          class="w-full h-[220px] object-cover rounded-xl"
        />
        <h3 class="text-base font-semibold text-on-surface mt-4">{{ candidate.title }}</h3>
        <p v-if="candidate.price != null" class="text-2xl font-bold text-primary mt-1">
          ¥{{ candidate.price }}
        </p>
        <div class="flex items-center gap-2 mt-2">
          <span class="text-xs px-2 py-0.5 rounded-full"
            :style="{
              backgroundColor: candidate.score >= 0.8 ? 'rgba(52,199,89,0.12)' : candidate.score >= 0.6 ? 'rgba(255,159,10,0.12)' : 'rgba(142,142,147,0.12)',
              color: candidate.score >= 0.8 ? '#34C759' : candidate.score >= 0.6 ? '#FF9F0A' : '#8E8E93'
            }">
            相似度 {{ scorePercent(candidate.score) }}%
          </span>
        </div>

        <div v-if="candidate.attrs && Object.keys(candidate.attrs).length > 0" class="mt-4">
          <h4 class="text-sm font-medium text-on-surface mb-1.5">关键属性</h4>
          <div v-for="(val, key) in candidate.attrs" :key="key" class="flex gap-1 py-0.5">
            <span class="text-sm text-on-surface-variant shrink-0">{{ key }}:</span>
            <span class="text-sm text-on-surface">{{ val }}</span>
          </div>
        </div>

        <!-- 无详情图时的提示 -->
        <div v-if="detailImageUrls.length === 0" class="mt-4">
          <p class="text-xs text-on-surface-variant/50">暂无详情图片</p>
        </div>

        <div class="mt-6">
          <button
            class="text-sm font-medium text-on-surface-variant hover:text-on-surface transition-colors"
            @click="emit('dismiss')"
          >
            关闭
          </button>
        </div>
      </div>

      <!-- 右侧：详情图片滚动区 -->
      <div v-if="detailImageUrls.length > 0" class="sm:w-1/2 p-5 overflow-y-auto max-h-[90vh] bg-surface-variant/5">
        <h4 class="text-sm font-medium text-on-surface mb-3 sticky top-0 bg-surface py-2 z-10">
          详情图片 ({{ detailImageUrls.length }}张)
        </h4>
        <div class="flex flex-col gap-3">
          <img
            v-for="(url, idx) in detailImageUrls"
            :key="idx"
            :src="resolveImageUrl(url)"
            :alt="`详情图 ${idx + 1}`"
            class="w-full object-contain rounded-lg border border-outline/10"
            loading="lazy"
          />
        </div>
      </div>

    </div>
  </div>
</template>
