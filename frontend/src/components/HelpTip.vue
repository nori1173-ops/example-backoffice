<script setup lang="ts">
import { useUiStore } from '@/stores/ui'

defineProps<{ text: string }>()

const ui = useUiStore()
</script>

<template>
  <!-- eager の既定 true では閉じていてもオーバーレイと document リスナが要素ごとに増える -->
  <v-tooltip
    :text="text"
    location="bottom"
    max-width="320"
    content-class="help-tip"
    :eager="false"
    :disabled="!ui.helpMode"
  >
    <template #activator="{ props }">
      <slot :props="props" />
    </template>
  </v-tooltip>
</template>

<style>
/* オーバーレイは body へ teleport されるため scoped にしない */
.help-tip {
  line-height: 1.5;
  white-space: normal;
}
</style>
