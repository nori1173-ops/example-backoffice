<script setup lang="ts">
import { computed } from 'vue'
import type { RunStatus } from '../types'

const props = defineProps<{ status: RunStatus }>()

const LABELS: Record<RunStatus, string> = {
  queued: '待機中',
  running: '実行中',
  succeeded: '成功',
  distributed: '配布済み',
  failed: '失敗',
}
const COLORS: Record<RunStatus, string> = {
  queued: 'grey',
  running: 'blue',
  succeeded: 'green',
  distributed: 'teal',
  failed: 'red',
}

const label = computed(() => LABELS[props.status])
const color = computed(() => COLORS[props.status])
</script>

<template>
  <v-chip
    :color="color"
    variant="flat"
    size="small"
    label
  >
    {{ label }}
  </v-chip>
</template>
