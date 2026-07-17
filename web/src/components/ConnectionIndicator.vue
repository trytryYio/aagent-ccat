<script setup lang="ts">
import type { ConnectionStatus } from '../types'

defineProps<{ status: ConnectionStatus }>()

const colorMap: Record<ConnectionStatus, string> = {
  connected: 'bg-status-green',
  disconnected: 'bg-status-red',
  checking: 'bg-gray-400',
}
</script>

<template>
  <span
    class="inline-block w-[7px] h-[7px] rounded-full mr-1"
    :class="colorMap[status]"
  />
</template>
