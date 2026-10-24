<script setup lang="ts">
import { computed } from 'vue'
import { formatCount, formatDateTime } from '@/utils/format'
import { isLogRef } from '../utils/logs'
import { isConsoleLink, peakHours } from '../utils/trace'
import type { EvidenceRow, JsonValue, LogRef } from '../types'
const props = defineProps<{ rows: (EvidenceRow | string)[]; label: string; logs?: boolean }>()
const emit = defineEmits<{ log: [ref: LogRef, link: string | undefined] }>()
// ref は「ログを見る」の問い合わせパラメータで、表には出さない
const hidden = new Set(['ref'])
const normalized = computed<EvidenceRow[]>(() => props.rows.map(row => typeof row === 'string' ? { name: row } : row))
const columns = computed(() => [...new Set(normalized.value.flatMap(row => Object.keys(row)))].filter(column => !hidden.has(column)))
const showLogs = computed(() => !!props.logs && normalized.value.some(row => isLogRef(row.ref)))
function openLog(row: EvidenceRow): void {
  if (isLogRef(row.ref)) emit('log', row.ref, isConsoleLink(row.link) ? row.link : undefined)
}
const labels: Record<string, string> = {
  actor: '主体', principal: '主体', ip: 'IP', cc: '国', country: '国', event: '操作', count: '件数',
  first_at: '開始（JST）', last_at: '終了（JST）', at: '時刻（JST）', time: '時刻（JST）',
  kind: '種類', previous_last_seen: '前回の利用日', last_seen: '最終観測', first_seen: '初回観測',
  user: 'ユーザー', username: 'ユーザー', name: '名前', id: 'ID', arn: 'ARN', slot: 'キーのスロット',
  last_used: '最終使用日', last_used_date: '最終使用日', password_last_used: 'パスワード最終使用日',
  mfa: 'MFA', unknown: '未使用期間不明', foreign: '国外', mfa_active: 'MFA', password_enabled: 'コンソール利用', created_at: '作成日時（JST）',
  cpu_avg: 'CPU 平均（%）', cpu_max: 'CPU 最大（%）', net_out: 'NetworkOut（bytes）',
  title: '内容', text: '内容', reason: '理由', severity: '重大度', region: 'リージョン',
  method: 'メソッド', path: 'パス', source: '取得元', status: '状態', days_unused: '未使用日数',
  event_id: 'イベント ID', where: '場所', link: 'コンソール', resource: '対象', remote_ip: '相手 IP', function: '関数', hourly: '時間帯',
  acl: 'Web ACL', category: '分類', rule: 'ルール', metric: '指標', blocked: '遮断', allowed: '許可',
}
function cell(value: JsonValue | undefined, key: string): string {
  if (value === undefined || value === null || value === '') return '—'
  if (key === 'hourly' && Array.isArray(value)) return peakHours(value)
  if (typeof value === 'number') return formatCount(value)
  if (typeof value === 'boolean') return value ? '有効' : 'なし'
  if (typeof value === 'string') return /(?:_at|_seen|^time)$/.test(key) && value.includes('T') ? formatDateTime(value) : value
  if (Array.isArray(value)) return value.map(item => cell(item, key)).join('、')
  return Object.entries(value).map(([field, item]) => `${labels[field] ?? field}: ${cell(item, field)}`).join(' / ')
}
</script>

<template>
  <div class="evidence-scroll">
    <v-table
      v-if="normalized.length"
      density="compact"
      :aria-label="label"
    >
      <thead>
        <tr>
          <th
            v-for="column in columns"
            :key="column"
            class="text-no-wrap"
          >
            {{ labels[column] ?? column }}
          </th>
          <th
            v-if="showLogs"
            class="text-no-wrap"
          >
            ログ
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(row, index) in normalized"
          :key="index"
        >
          <td
            v-for="column in columns"
            :key="column"
          >
            <a
              v-if="column === 'link' && isConsoleLink(row[column])"
              :href="row[column]"
              target="_blank"
              rel="noopener"
            >コンソール</a>
            <template v-else>
              {{ cell(row[column], column) }}
            </template>
          </td>
          <td v-if="showLogs">
            <v-btn
              v-if="isLogRef(row.ref)"
              data-test="log-button"
              size="x-small"
              variant="tonal"
              @click="openLog(row)"
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
      該当する明細はありません。
    </p>
  </div>
</template>

<style scoped>
.evidence-scroll { overflow-x: auto; }
td { overflow-wrap: anywhere; min-width: 90px; }
</style>
