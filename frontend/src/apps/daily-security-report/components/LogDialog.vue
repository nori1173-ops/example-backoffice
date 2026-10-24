<script setup lang="ts">
import { computed } from 'vue'
import HelpTip from '@/components/HelpTip.vue'
import { formatCount, formatDateTime } from '@/utils/format'
import { HELP } from '../help'
import { GUARDDUTY_FIELDS, LOG_COLUMNS, describeLogRef, formatLogCell, formatScanned } from '../utils/logs'
import { isConsoleLink } from '../utils/trace'
import type { Glossary, LogQueryResult, LogRef, LogRow } from '../types'
import type { LogQueryState } from '../composables/useLogQuery'

const props = defineProps<{
  modelValue: boolean
  logRef: LogRef | null
  status: LogQueryState
  result: LogQueryResult | null
  error: string | null
  errorCode?: string | null
  link?: string | null
  glossary?: Glossary | null
}>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
const kind = computed(() => props.logRef?.kind ?? null)
const subject = computed(() => props.logRef ? describeLogRef(props.logRef, props.glossary) : '')
const rows = computed<LogRow[]>(() => props.status === 'succeeded' ? props.result?.rows ?? [] : [])
const columns = computed(() => kind.value && kind.value !== 'guardduty' ? LOG_COLUMNS[kind.value] : [])
const consoleLink = computed(() => [props.link, props.result?.link, kind.value === 'guardduty' ? rows.value[0]?.link : null].find(isConsoleLink) ?? null)
const scanned = computed(() => formatScanned(props.result?.scanned_bytes))
function fields(row: LogRow) {
  const known = new Set([...GUARDDUTY_FIELDS.map(field => field.key), 'link', 'time'])
  return [...GUARDDUTY_FIELDS, ...Object.keys(row).filter(key => !known.has(key)).map(key => ({ key, label: key }))]
}
const close = () => emit('update:modelValue', false)
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    max-width="1100"
    scrollable
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card data-test="log-dialog">
      <v-card-title class="d-flex align-center flex-wrap ga-2 text-wrap">
        <HelpTip :text="HELP.logs">
          <template #default="{ props: tip }">
            <span v-bind="tip">ログを見る</span>
          </template>
        </HelpTip>
        <span class="text-body-1 text-medium-emphasis log-subject">{{ subject }}</span>
      </v-card-title>
      <v-card-text>
        <div
          v-if="status === 'running'"
          data-test="log-running"
        >
          <v-progress-linear
            indeterminate
            class="mb-2"
          />
          <p class="text-body-2">
            問い合わせ中（2 秒ごとに結果を確認、最大 90 秒）
          </p>
        </div>
        <v-alert
          v-else-if="status === 'failed'"
          data-test="log-error"
          type="error"
          variant="tonal"
        >
          取得できませんでした: {{ error }}<span v-if="errorCode && !error?.includes(errorCode)">（{{ errorCode }}）</span>
        </v-alert>
        <template v-else-if="status === 'succeeded' && result">
          <dl
            class="log-meta text-body-2 mb-3"
            data-test="log-meta"
          >
            <dt>取得元</dt><dd>{{ result.source }}</dd>
            <dt>期間</dt><dd>{{ formatDateTime(result.range?.from) }} 〜 {{ formatDateTime(result.range?.to) }}（JST）</dd>
            <dt>行数</dt>
            <dd>
              {{ formatCount(rows.length) }} 行<span v-if="result.truncated">（先頭 {{ formatCount(rows.length) }} 行。上限で打ち切り）</span>
            </dd>
            <template v-if="scanned">
              <dt>走査量</dt><dd>{{ scanned }}</dd>
            </template>
          </dl>
          <p
            v-if="result.note"
            class="text-caption mb-2"
          >
            {{ result.note }}
          </p>
          <p
            v-if="!rows.length"
            class="text-body-2 text-medium-emphasis"
          >
            該当するログはありません。
          </p>
          <template v-else-if="kind === 'guardduty'">
            <v-table
              v-for="(row, index) in rows"
              :key="index"
              density="compact"
              class="mb-3"
              aria-label="GuardDuty の検出"
              data-test="log-fields"
            >
              <tbody>
                <tr
                  v-for="field in fields(row)"
                  :key="field.key"
                >
                  <th class="text-no-wrap">
                    {{ field.label }}
                  </th>
                  <td class="log-text">
                    {{ formatLogCell(row[field.key], field.key, 'guardduty') }}
                  </td>
                </tr>
              </tbody>
            </v-table>
          </template>
          <div
            v-else
            class="log-scroll"
          >
            <v-table
              density="compact"
              fixed-header
              aria-label="ログ"
              data-test="log-table"
            >
              <thead>
                <tr>
                  <th
                    v-for="column in columns"
                    :key="column.key"
                    class="text-no-wrap"
                  >
                    {{ column.label }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(row, index) in rows"
                  :key="index"
                >
                  <td
                    v-for="column in columns"
                    :key="column.key"
                    :class="column.key === 'time' ? 'text-no-wrap' : 'log-text'"
                  >
                    <a
                      v-if="column.key === 'event_id' && isConsoleLink(row.link)"
                      :href="row.link"
                      target="_blank"
                      rel="noopener"
                    >{{ formatLogCell(row[column.key], column.key, kind!) }}</a>
                    <template v-else>
                      {{ formatLogCell(row[column.key], column.key, kind!) }}
                    </template>
                  </td>
                </tr>
              </tbody>
            </v-table>
          </div>
        </template>
      </v-card-text>
      <v-card-actions>
        <v-btn
          v-if="consoleLink"
          data-test="log-console"
          :href="consoleLink"
          target="_blank"
          rel="noopener"
          prepend-icon="mdi-open-in-new"
        >
          コンソールで開く
        </v-btn>
        <v-spacer />
        <v-btn
          data-test="log-close"
          @click="close"
        >
          閉じる
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.log-subject { overflow-wrap: anywhere; }
.log-meta { display: grid; grid-template-columns: max-content 1fr; column-gap: 12px; row-gap: 2px; }
.log-meta dd { overflow-wrap: anywhere; }
.log-scroll { overflow-x: auto; }
.log-text { white-space: pre-wrap; overflow-wrap: anywhere; min-width: 120px; }
</style>
