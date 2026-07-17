<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  previewUrl: string | null
  isStreaming: boolean
  isUploading: boolean
}>()

const emit = defineEmits<{
  send: [text: string]
  stop: []
  pickImage: []
  clearImage: []
}>()

const text = ref('')
</script>

<template>
  <div class="bg-surface shadow-[0_-4px_12px_rgba(0,0,0,0.06)] shrink-0">
    <div class="px-3 py-2">
      <!-- Selected image -->
      <div v-if="previewUrl" class="flex items-center justify-between mb-2">
        <img :src="previewUrl" alt="已选图片" class="w-11 h-11 rounded-lg object-cover" />
        <button class="p-1 rounded-full hover:bg-surface-variant transition-colors" @click="emit('clearImage')">
          <svg class="w-[18px] h-[18px] text-on-surface-variant" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <!-- Upload progress -->
      <div v-if="isUploading" class="w-full h-[3px] mb-2 rounded-full bg-outline/15 overflow-hidden">
        <div class="h-full rounded-full bg-primary/30 animate-pulse" style="width: 60%" />
      </div>

      <!-- Input row -->
      <div class="flex items-center gap-1.5">
        <button class="p-2 rounded-lg text-on-surface/60 hover:bg-surface-variant transition-colors" @click="emit('pickImage')">
          <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6.827 6.175A2.31 2.31 0 015.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 00-1.134-.175 2.31 2.31 0 01-1.64-1.055l-.822-1.316a2.192 2.192 0 00-1.736-1.039 48.774 48.774 0 00-5.232 0 2.192 2.192 0 00-1.736 1.039l-.821 1.316z" />
            <path stroke-linecap="round" stroke-linejoin="round" d="M16.5 12.75a4.5 4.5 0 11-9 0 4.5 4.5 0 019 0z" />
          </svg>
        </button>

        <input
          v-model="text"
          type="text"
          placeholder="输入消息..."
          class="flex-1 h-10 px-4 text-sm rounded-full outline-none transition-colors"
          :style="{
            border: '1px solid',
            borderColor: text ? 'rgba(99,99,102,0.5)' : 'rgba(99,99,102,0.25)',
            backgroundColor: 'rgba(240,239,237,0.4)',
          }"
          @keydown.enter="
            (text.trim() || previewUrl) && !isStreaming
              ? (emit('send', text), text = '')
              : null
          "
        />

        <button
          v-if="isStreaming"
          class="w-10 h-10 rounded-full flex items-center justify-center hover:bg-surface-variant transition-colors"
          @click="emit('stop')"
        >
          <svg class="w-5 h-5" style="color: #BA3B3B" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <rect x="6" y="6" width="12" height="12" rx="1" />
          </svg>
        </button>

        <button
          v-else
          class="w-10 h-10 rounded-full flex items-center justify-center transition-colors"
          :class="text.trim() || previewUrl ? 'bg-primary' : 'bg-outline/15'"
          @click="(text.trim() || previewUrl) && (emit('send', text), text = '')"
        >
          <svg
            class="w-[18px] h-[18px]"
            :class="text.trim() || previewUrl ? 'text-on-primary' : 'text-outline'"
            fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
          >
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>
