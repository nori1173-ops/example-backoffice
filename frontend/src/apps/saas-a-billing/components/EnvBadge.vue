<script setup lang="ts">
import { computed } from 'vue'
import type { TargetEnv } from '../types'

const props = defineProps<{ targetEnv: TargetEnv }>()

// 検証環境の結果を本番と取り違えないための色分け（設計 3.3 / 3.4）
const LABELS: Record<TargetEnv, string> = {
  verification: '検証環境',
  production: '本番',
}
const COLORS: Record<TargetEnv, string> = {
  verification: 'orange',
  production: 'green',
}

const label = computed(() => LABELS[props.targetEnv])
const color = computed(() => COLORS[props.targetEnv])
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
