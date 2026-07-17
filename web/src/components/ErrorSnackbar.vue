<script setup lang="ts">
defineProps<{
  message: string
}>()
const emit = defineEmits<{
  dismiss: []
  retry: []
}>()
</script>

<template>
  <div
    class="flex items-center gap-2 px-3.5 py-2.5 rounded-xl shadow-lg"
    :style="{ backgroundColor: '#FDE8E8', color: '#BA3B3B' }"
  >
    <svg class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
      <path stroke-linecap="round" stroke-linejoin="round" d="M18.364 5.636a9 9 0 11-12.728 0 9 9 0 0112.728 0zM12 9v4m0 4h.01" />
    </svg>
    <span class="text-xs flex-1">{{ message }}</span>
    <button class="text-xs font-medium hover:opacity-80" @click="emit('retry')">重试</button>
    <button class="text-xs font-medium hover:opacity-80" @click="emit('dismiss')">关闭</button>
  </div>
</template>
