<script setup lang="ts">
import type { PipelineStep } from '../types'

defineProps<{
  steps: PipelineStep[]
}>()

function getStepIcon(status: PipelineStep['status']): string {
  switch (status) {
    case 'done': return '✅'
    case 'running': return '⏳'
    case 'error': return '❌'
    default: return '⚪'
  }
}

function getStepClass(status: PipelineStep['status']): string {
  switch (status) {
    case 'done': return 'text-on-surface/70'
    case 'running': return 'text-primary font-medium'
    case 'error': return 'text-error'
    default: return 'text-on-surface-variant/50'
  }
}
</script>

<template>
  <div v-if="steps.length > 0" class="pipeline-progress space-y-1 py-2">
    <div
      v-for="step in steps"
      :key="step.step"
      class="flex items-center gap-2 text-xs"
      :class="getStepClass(step.status)"
    >
      <span class="text-sm leading-none">{{ getStepIcon(step.status) }}</span>
      <span>{{ step.label }}</span>
      <!-- 正在运行时的加载动画 -->
      <span
        v-if="step.status === 'running'"
        class="inline-block w-3 h-3 border-2 border-primary/30 border-t-primary rounded-full animate-spin"
      />
    </div>
  </div>
</template>

<style scoped>
@keyframes spin {
  to { transform: rotate(360deg); }
}
.animate-spin {
  animation: spin 0.8s linear infinite;
}
</style>
