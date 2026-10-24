<script setup lang="ts">
import { computed } from 'vue'
import HelpTip from '@/components/HelpTip.vue'
import { DASH, formatCount } from '@/utils/format'
import LevelChip from './LevelChip.vue'
import { isMetricCounter } from '../utils/logs'
import { formatBytes } from '../utils/format'
import { counterRows, formatBaseline, formatRatio, hourlyBreakdown, peakHours, type CounterRow } from '../utils/trace'
import type { Comparison, Glossary, LogRef } from '../types'
const props = defineProps<{
  label: string
  counters: Record<string, number>
  comparisons?: Record<string, Comparison> | null
  hourly?: Record<string, number[]> | null
  glossary?: Glossary | null
  aiKeys?: string[] | null
  logs?: boolean
}>()
const emit = defineEmits<{ log: [ref: LogRef] }>()
const rows = computed(() => counterRows(props))
const showLogs = computed(() => !!props.logs && rows.value.some(row => isMetricCounter(row.key)))
function baseline(row: CounterRow): string {
  if (!row.comparison) return DASH
  return row.comparison.label || (row.key === 's3.bytes_downloaded'
    ? formatBytes(row.comparison.baseline)
    : formatBaseline(row.comparison.baseline))
}
</script>

<template>
  <div class="counter-scroll">
    <v-table
      v-if="rows.length"
      density="compact"
      :aria-label="label"
      data-test="counter-table"
    >
      <thead>
        <tr>
          <th>指標</th><th>件数</th><th>基準</th><th>比率</th><th>判定</th><th>時間帯</th><th
            v-if="showLogs"
            class="text-no-wrap"
          >
            ログ
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.key"
          data-test="counter-row"
          :class="{ exceeded: row.comparison?.exceeded }"
        >
          <td>
            <div class="d-flex align-center flex-wrap ga-1">
              <HelpTip
                v-if="row.description"
                :text="row.description"
              >
                <template #default="{ props: tip }">
                  <span v-bind="tip">{{ row.label }}</span>
                </template>
              </HelpTip>
              <span v-else>{{ row.label }}</span>
              <v-chip
                v-if="row.ai"
                data-test="ai-key-chip"
                size="x-small"
                variant="tonal"
                color="primary"
                label
              >
                AI の根拠
              </v-chip>
            </div>
            <div
              v-if="row.label !== row.key"
              class="text-caption text-medium-emphasis counter-key"
            >
              {{ row.key }}
            </div>
          </td>
          <td>{{ row.key === 's3.bytes_downloaded' ? formatBytes(row.value) : formatCount(row.value) }}</td>
          <td>{{ baseline(row) }}</td>
          <td>{{ formatRatio(row.comparison?.ratio) }}</td>
          <td>
            <LevelChip
              v-if="row.comparison?.exceeded"
              level="warning"
            />
            <v-chip
              v-else-if="row.comparison?.label"
              color="grey"
              size="small"
              label
            >
              {{ row.comparison.label }}
            </v-chip>
          </td>
          <td class="text-no-wrap">
            <v-tooltip
              v-if="row.hourly"
              :text="hourlyBreakdown(row.hourly)"
              location="bottom"
              max-width="360"
              :eager="false"
            >
              <template #activator="{ props: tip }">
                <span
                  v-bind="tip"
                  data-test="counter-hourly"
                >{{ peakHours(row.hourly) }}</span>
              </template>
            </v-tooltip>
            <template v-else>
              {{ DASH }}
            </template>
          </td>
          <td v-if="showLogs">
            <v-btn
              v-if="isMetricCounter(row.key)"
              data-test="log-button"
              size="x-small"
              variant="tonal"
              @click="emit('log', { kind: 'metric', key: row.key })"
            >
              ログを見る
            </v-btn>
          </td>
        </tr>
      </tbody>
    </v-table>
    <p
      v-else
      class="text-body-2 text-medium-emphasis"
    >
      集計値はありません。
    </p>
  </div>
</template>

<style scoped>
.counter-scroll { overflow-x: auto; }
td { overflow-wrap: anywhere; }
.counter-key { overflow-wrap: anywhere; }
.exceeded td { background-color: rgba(var(--v-theme-warning), 0.12); }
</style>
