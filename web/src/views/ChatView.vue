<script setup lang="ts">
import { ref } from 'vue'
import { useChat } from '../composables/useChat'
import TopBar from '../components/TopBar.vue'
import EmptyState from '../components/EmptyState.vue'
import MessageList from '../components/MessageList.vue'
import ChatBottomBar from '../components/ChatBottomBar.vue'
import ErrorSnackbar from '../components/ErrorSnackbar.vue'
import ImageUploadDialog from '../components/ImageUploadDialog.vue'

const {
  state,
  sendMessage,
  stopGeneration,
  sendClarification,
  clearError,
  retryLastMessage,
  newSession,
  selectImage,
  clearSelectedImage,
} = useChat()

const showImagePicker = ref(false)

function onPickImage() {
  showImagePicker.value = true
}

function onFileSelect(file: File) {
  selectImage(file)
  showImagePicker.value = false
}

function onSend(text: string) {
  sendMessage(text || null)
}
</script>

<template>
  <div class="h-full flex flex-col bg-bg">
    <TopBar
      :connection-status="state.connectionStatus"
      @new-session="newSession"
    />

    <template v-if="state.messages.length === 0">
      <EmptyState
        v-model:preview-url="state.selectedImagePreviewUrl"
        @send-message="onSend"
        @pick-image="onPickImage"
        @clear-image="clearSelectedImage"
      />
    </template>

    <template v-else>
      <MessageList
        :messages="state.messages"
        @clarify-send="sendClarification"
      />
    </template>

    <!-- Bottom bar (only when messages exist) -->
    <ChatBottomBar
      v-if="state.messages.length > 0"
      :preview-url="state.selectedImagePreviewUrl"
      :is-streaming="state.isStreaming"
      :is-uploading="state.isUploading"
      @send="onSend"
      @stop="stopGeneration"
      @pick-image="onPickImage"
      @clear-image="clearSelectedImage"
    />

    <!-- Error snackbar -->
    <div
      v-if="state.error"
      class="px-3 pb-2"
    >
      <ErrorSnackbar
        :message="state.error"
        @dismiss="clearError"
        @retry="retryLastMessage"
      />
    </div>

    <!-- Image picker dialog -->
    <ImageUploadDialog
      v-if="showImagePicker"
      @select="onFileSelect"
      @dismiss="showImagePicker = false"
    />
  </div>
</template>
