<script setup lang="ts">
import { ref } from 'vue'
import type { Candidate } from '../types'
import CandidateDetail from './CandidateDetail.vue'
import { resolveImageUrl } from '../utils/image'

const props = defineProps<{ candidate: Candidate }>()
const showDetail = ref(false)

function scoreColor(score: number): string {
  if (score >= 0.8) return '#4A7C59'
  if (score >= 0.6) return '#B8860B'
  return '#8B7355'
}

function scorePercent(score: number): number {
  return Math.round(score * 100)
}
</script>

<template>
  <div
    class="candidate-card w-[148px] shrink-0 rounded-xl bg-surface shadow-sm overflow-hidden cursor-pointer select-none active:scale-[0.96] transition-transform"
    @click="showDetail = true"
  >
    <div class="relative">
      <img
        :src="resolveImageUrl(candidate.image_url)"
        :alt="candidate.title"
        class="w-full h-[124px] object-cover rounded-t-xl"
        loading="lazy"
      />
      <span
        class="absolute top-0 right-0 px-1.5 py-0.5 text-[11px] font-semibold text-white rounded-bl-lg"
        :style="{ backgroundColor: scoreColor(candidate.score) + 'E6' }"
      >
        {{ scorePercent(candidate.score) }}%
      </span>
    </div>
    <div class="p-2.5">
      <p class="text-xs font-medium text-on-surface line-clamp-2 leading-tight">
        {{ candidate.title }}
      </p>
      <p v-if="candidate.price != null" class="text-sm font-semibold text-primary mt-0.5">
        ¥{{ candidate.price }}
      </p>
    </div>
  </div>

  <CandidateDetail
    v-if="showDetail"
    :candidate="candidate"
    @dismiss="showDetail = false"
  />
</template>
