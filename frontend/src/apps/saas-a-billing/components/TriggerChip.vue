<script setup lang="ts">
import { computed } from 'vue'
import HelpTip from '@/components/HelpTip.vue'
import { HELP } from '../help'
import type { RunTrigger } from '../types'

// 過去の RUN には `trigger` が無いことがある（API は属性をそのまま通すだけ）
const props = defineProps<{ trigger?: RunTrigger; dataTest?: string }>()

// 状態チップ（flat の濃色）と読み違えないよう tonal の淡色にする（#1013）
const LABELS: Record<RunTrigger, string> = {
  scheduled: '自動',
  manual: '手動',
}
const COLORS: Record<RunTrigger, string> = {
  scheduled: 'primary',
  manual: 'grey',
}

// 想定外の値・欠損はそのまま出さず、チップごと出さない（API の内部値を画面に漏らさない）
const label = computed(() => (props.trigger ? LABELS[props.trigger] : undefined))
const color = computed(() => (props.trigger ? COLORS[props.trigger] : undefined))
</script>

<template>
  <span v-if="label">
    <HelpTip :text="HELP.runList.trigger">
      <template #default="{ props: tip }">
        <v-chip
          v-bind="tip"
          :color="color"
          :data-test="dataTest"
          variant="tonal"
          size="x-small"
          label
        >
          {{ label }}
        </v-chip>
      </template>
    </HelpTip>
  </span>
</template>
