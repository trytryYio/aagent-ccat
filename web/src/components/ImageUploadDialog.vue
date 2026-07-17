<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{
  select: [file: File]
  dismiss: []
}>()

const fileInput = ref<HTMLInputElement | null>(null)

function openFilePicker() {
  fileInput.value?.click()
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files && input.files.length > 0) {
    emit('select', input.files[0])
    input.value = ''
  }
}

function onBackdropClick(e: MouseEvent) {
  if ((e.target as HTMLElement).classList.contains('dialog-backdrop')) {
    emit('dismiss')
  }
}
</script>

<template>
  <div
    class="dialog-backdrop fixed inset-0 z-50 flex items-center justify-center bg-black/30"
    @click="onBackdropClick"
  >
    <div class="bg-surface rounded-2xl p-6 shadow-xl min-w-[280px]">
      <h3 class="text-base font-semibold text-on-surface mb-4">选择图片来源</h3>
      <div class="flex gap-4 justify-center">
        <button
          class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-on-surface hover:bg-surface-variant transition-colors"
          @click="openFilePicker"
        >
          <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5" />
          </svg>
          上传图片
        </button>
      </div>
      <div class="flex justify-end mt-4">
        <button
          class="text-sm font-medium text-on-surface-variant hover:text-on-surface transition-colors"
          @click="emit('dismiss')"
        >
          取消
        </button>
      </div>
    </div>
    <input
      ref="fileInput"
      type="file"
      accept="image/jpeg,image/png,image/webp"
      class="hidden"
      @change="onFileChange"
    />
  </div>
</template>
