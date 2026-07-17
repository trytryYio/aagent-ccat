<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { getBaseUrl, setBaseUrl } from '../api/client'

const router = useRouter()
const url = ref(getBaseUrl())

function save() {
  setBaseUrl(url.value)
}
</script>

<template>
  <div class="h-full flex flex-col bg-bg">
    <header class="flex items-center gap-2 px-4 h-14 bg-bg shrink-0">
      <button class="p-2 rounded-lg text-on-surface/60 hover:bg-surface-variant transition-colors" @click="router.back()">
        <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d="M10.5 19.5L3 12m0 0l7.5-7.5M3 12h18" />
        </svg>
      </button>
      <h1 class="text-lg font-semibold text-on-surface">设置</h1>
    </header>

    <div class="p-4 space-y-4">
      <h2 class="text-sm font-medium text-on-surface">服务地址</h2>
      <input
        v-model="url"
        type="text"
        placeholder="http://localhost:8000"
        class="w-full px-3 py-2 text-sm rounded-lg border border-outline bg-surface text-on-surface outline-none focus:border-on-surface-variant transition-colors"
      />
      <button
        class="w-full py-2.5 rounded-lg text-sm font-medium bg-primary text-on-primary hover:opacity-90 transition-opacity"
        @click="save"
      >
        保存
      </button>
      <p class="text-xs text-on-surface-variant/60 leading-relaxed">
        默认地址 http://localhost:8000 指向本地后端服务。<br />
        部署到其他地址时，请替换为实际的服务器地址。
      </p>
    </div>
  </div>
</template>
