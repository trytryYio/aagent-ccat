<script setup lang="ts">
import { ref } from 'vue'

defineProps<{ question: string }>()
const emit = defineEmits<{ send: [text: string] }>()
const input = ref('')
</script>

<template>
  <div class="mt-2 p-3.5 rounded-xl" style="background-color: rgba(44, 44, 46, 0.06);">
    <p class="text-sm font-medium text-on-surface">{{ question }}</p>
    <div class="flex items-center gap-2 mt-2.5">
      <input
        v-model="input"
        type="text"
        placeholder="请输入..."
        class="flex-1 px-3 py-2 text-sm rounded-full border outline-none transition-colors"
        :style="{
          borderColor: input ? 'rgba(99,99,102,0.4)' : 'rgba(99,99,102,0.2)',
          backgroundColor: 'transparent',
        }"
        @keydown.enter="input.trim() && (emit('send', input), input = '')"
      />
      <button
        class="shrink-0 px-4 py-2 rounded-full text-sm font-medium text-white transition-opacity disabled:opacity-40"
        :class="input.trim() ? 'bg-primary' : 'bg-on-surface-variant/20 text-on-surface-variant'"
        :disabled="!input.trim()"
        @click="emit('send', input); input = ''"
      >
        发送
      </button>
    </div>
  </div>
</template>
