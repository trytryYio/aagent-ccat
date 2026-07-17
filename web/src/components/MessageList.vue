<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import type { ChatMessage } from '../types'
import MessageBubble from './MessageBubble.vue'
import { useMessageAnimation } from '../composables/useGSAP'

const props = defineProps<{ messages: ChatMessage[] }>()
const emit = defineEmits<{ clarifySend: [text: string] }>()
const listRef = ref<HTMLDivElement | null>(null)
const { animateIn } = useMessageAnimation()

watch(
  () => props.messages.length,
  async (n, old) => {
    await nextTick()
    if (listRef.value) {
      listRef.value.scrollTop = listRef.value.scrollHeight
      // 新增消息淡入上滑
      if (n > (old ?? 0)) {
        const last = listRef.value.querySelector('.message-bubble:last-child') as HTMLElement | null
        if (last) animateIn(last)
      }
    }
  },
  { flush: 'post' },
)
</script>

<template>
  <div
    ref="listRef"
    class="message-list flex-1 overflow-y-auto px-3 py-2.5 space-y-3.5"
  >
    <MessageBubble
      v-for="msg in messages"
      :key="msg.id"
      :message="msg"
      @clarify-send="emit('clarifySend', $event)"
    />
  </div>
</template>
