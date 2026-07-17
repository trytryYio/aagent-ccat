<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{
  sendMessage: [text: string]
  pickImage: []
  clearImage: []
}>()

const text = ref('')
const previewUrl = defineModel<string | null>('previewUrl', { required: true })
</script>

<template>
  <div class="flex-1 flex flex-col bg-gradient-empty">
    <div class="flex-1 flex flex-col items-center justify-center px-8">
      <div class="flex-1" />

      <!-- Tagline -->
      <p class="text-lg tracking-[3px] text-on-surface/35 font-normal">
        买对不贵，直达好物
      </p>

      <div class="mt-7" />

      <!-- Search input -->
      <div class="w-full max-w-md bg-white rounded-full shadow-sm flex items-center px-4 py-1">
        <input
          v-model="text"
          type="text"
          placeholder="搜索商品"
          class="flex-1 h-11 text-sm text-on-surface bg-transparent outline-none placeholder:text-on-surface/20 tracking-wide"
          @keydown.enter="text.trim() && (emit('sendMessage', text), text = '')"
        />
        <span class="w-[1.5px] h-[18px] rounded-sm shrink-0 mx-1" style="background-color: rgba(44,44,46,0.2)" />
        <button
          class="shrink-0 w-9 h-9 rounded-full flex items-center justify-center transition-colors"
          :class="text.trim() || previewUrl ? 'bg-primary' : 'bg-transparent'"
          @click="emit('sendMessage', text); text = ''"
        >
          <svg
            class="w-[18px] h-[18px]"
            :class="text.trim() || previewUrl ? 'text-on-primary' : 'text-outline/30'"
            fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"
          >
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 12L3.269 3.126A59.768 59.768 0 0121.485 12 59.77 59.77 0 013.27 20.876L5.999 12zm0 0h7.5" />
          </svg>
        </button>
      </div>

      <!-- Selected image preview -->
      <div v-if="previewUrl" class="mt-4 w-full max-w-md">
        <div class="bg-white rounded-xl shadow-sm flex items-center gap-3 p-2">
          <img :src="previewUrl" alt="已选图片" class="w-12 h-12 rounded-lg object-cover shrink-0" />
          <span class="text-sm text-on-surface/40">1 张图片已选择</span>
          <button class="ml-auto p-1 rounded-full hover:bg-surface-variant transition-colors" @click="emit('clearImage')">
            <svg class="w-[18px] h-[18px] text-on-surface/30" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      <!-- Camera entry (only when no image selected) -->
      <div v-else class="mt-6">
        <button
          class="w-11 h-11 rounded-full flex items-center justify-center transition-colors hover:bg-white/60"
          style="background-color: rgba(255,255,255,0.5)"
          @click="emit('pickImage')"
        >
          <svg class="w-5 h-5 text-on-surface/30" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6.827 6.175A2.31 2.31 0 015.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 00-1.134-.175 2.31 2.31 0 01-1.64-1.055l-.822-1.316a2.192 2.192 0 00-1.736-1.039 48.774 48.774 0 00-5.232 0 2.192 2.192 0 00-1.736 1.039l-.821 1.316z" />
            <path stroke-linecap="round" stroke-linejoin="round" d="M16.5 12.75a4.5 4.5 0 11-9 0 4.5 4.5 0 019 0z" />
          </svg>
        </button>
      </div>

      <div class="flex-1" />
    </div>
  </div>
</template>
