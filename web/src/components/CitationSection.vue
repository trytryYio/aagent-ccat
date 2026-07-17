<script setup lang="ts">
import { ref } from 'vue'
import type { Citation } from '../types'

defineProps<{ citations: Citation[] }>()
const expanded = ref(false)
</script>

<template>
  <div class="mt-2">
    <button
      class="flex items-center gap-1 text-xs text-on-surface-variant/60 hover:text-on-surface-variant transition-colors"
      @click="expanded = !expanded"
    >
      <svg
        class="w-4 h-4 transition-transform"
        :class="{ 'rotate-180': expanded }"
        fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
      >
        <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7" />
      </svg>
      引用来源 ({{ citations.length }})
    </button>
    <div v-if="expanded" class="mt-2 space-y-1">
      <div
        v-for="(citation, idx) in citations"
        :key="citation.chunk_id"
        class="pl-1"
      >
        <p class="text-[11px] font-medium text-on-surface-variant/70">
          [{{ idx + 1 }}] {{ citation.source }}
        </p>
        <p class="text-xs text-on-surface-variant/70 mt-0.5 leading-relaxed">
          "{{ citation.snippet }}"
        </p>
        <div v-if="idx < citations.length - 1" class="my-1.5 border-t border-outline/30" />
      </div>
    </div>
  </div>
</template>
