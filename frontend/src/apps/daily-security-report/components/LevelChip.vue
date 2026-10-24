<script setup lang="ts">
import type { Level } from '../types'
defineProps<{ level?: Level | null; legacy?: boolean }>()
const levels = { critical: { color: 'error', label: 'Critical' }, warning: { color: 'warning', label: 'Warning' }, ok: { color: 'success', label: 'OK' } }
</script>

<template>
  <span class="d-inline-flex align-center ga-1">
    <v-chip
      v-if="level"
      :color="levels[level].color"
      size="small"
      label
    >{{ levels[level].label }}</v-chip>
    <span v-else>—</span>
    <v-chip
      v-if="legacy"
      color="grey"
      size="small"
      label
    >v1</v-chip>
  </span>
</template>
