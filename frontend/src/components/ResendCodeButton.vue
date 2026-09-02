<script setup lang="ts">
import { onUnmounted, ref } from 'vue'

const props = withDefaults(
  defineProps<{
    label?: string
    cooldownSeconds?: number
    loading?: boolean
  }>(),
  { label: '確認コードを再送する', cooldownSeconds: 30, loading: false },
)

const emit = defineEmits<{
  resend: []
}>()

const remaining = ref(0)
let timer: ReturnType<typeof setInterval> | null = null

function stop(): void {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

// 送信の成否に関わらず抑止する（失敗の連打こそ Cognito の送信上限に当たりやすい）
function onClick(): void {
  if (remaining.value > 0 || props.loading) {
    return
  }
  stop()
  remaining.value = props.cooldownSeconds
  timer = setInterval(() => {
    remaining.value -= 1
    if (remaining.value <= 0) {
      stop()
    }
  }, 1000)
  emit('resend')
}

onUnmounted(stop)
</script>

<template>
  <div class="text-center">
    <v-btn
      variant="text"
      size="small"
      prepend-icon="mdi-email-sync-outline"
      :disabled="remaining > 0"
      :loading="loading"
      data-test="resend-code"
      @click="onClick"
    >
      {{ remaining > 0 ? `再送できます（あと ${remaining} 秒）` : label }}
    </v-btn>
  </div>
</template>
