<script setup lang="ts">
import { onActivated, onUnmounted, ref, watch } from 'vue'
import { DASH, formatDateTimeSeconds } from '@/utils/format'
import { useLogsStore, type LogRange } from '../stores/logs'
import type { AuditEntry, AuditTarget } from '../types'
import { AUDIT_TARGET_LABELS, summarize } from '../utils/audit'

/** 1 文字ごとに絞り込むと長い一覧で入力が詰まる（UI-03） */
const SEARCH_DEBOUNCE_MS = 300
const SYSTEM_ACTOR = 'system'

const RANGE_ITEMS: { title: string; value: LogRange }[] = [
  { title: '直近 7 日', value: '7d' },
  { title: '直近 30 日', value: '30d' },
  { title: '直近 90 日', value: '90d' },
  { title: 'すべて', value: 'all' },
]

const TARGET_ITEMS: { title: string; value: '' | AuditTarget }[] = [
  { title: 'すべて', value: '' },
  { title: AUDIT_TARGET_LABELS.run, value: 'run' },
  { title: AUDIT_TARGET_LABELS.settings, value: 'settings' },
  { title: AUDIT_TARGET_LABELS.price, value: 'price' },
]

const TARGET_COLORS: Record<AuditTarget, string | undefined> = {
  run: 'primary',
  settings: 'secondary',
  price: undefined,
}

const store = useLogsStore()
const search = ref(store.query)
const detail = ref<AuditEntry | null>(null)
const detailDialog = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null

function actorLabel(entry: AuditEntry): string {
  return entry.actor === SYSTEM_ACTOR ? 'システム' : entry.actor_email || DASH
}

function toJson(values: Record<string, unknown> | null): string {
  return values === null ? DASH : JSON.stringify(values, null, 2)
}

function openDetail(entry: AuditEntry): void {
  detail.value = entry
  detailDialog.value = true
}

function stopDebounce(): void {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
}

watch(search, (value) => {
  stopDebounce()
  timer = setTimeout(() => {
    store.query = value
  }, SEARCH_DEBOUNCE_MS)
})

// 期間と種別はサーバ側で絞るため、変えたらその場で取り直す
watch([() => store.range, () => store.target], () => void store.fetch())

// keep-alive 配下では onMounted が再発火しないため、画面に入るたびに取り直す（UI-04）
onActivated(() => void store.fetch())

onUnmounted(stopDebounce)
</script>

<template>
  <div>
    <div class="mb-4">
      <h1 class="text-h5 mb-1">
        ログ
      </h1>
      <p class="text-body-2 text-medium-emphasis mb-0">
        誰がいつ何を行ったかの記録です。新しい順に表示します。
      </p>
    </div>

    <v-card
      variant="flat"
      border
      class="mb-4"
    >
      <v-card-text class="d-flex align-center flex-wrap ga-3">
        <v-select
          v-model="store.range"
          data-test="logs-range"
          label="期間"
          density="compact"
          variant="outlined"
          hide-details
          class="logs-filter"
          :items="RANGE_ITEMS"
        />
        <v-select
          v-model="store.target"
          data-test="logs-target"
          label="種別"
          density="compact"
          variant="outlined"
          hide-details
          class="logs-filter"
          :items="TARGET_ITEMS"
        />
        <v-text-field
          v-model="search"
          data-test="logs-search"
          label="実行者・内容で検索"
          density="compact"
          variant="outlined"
          hide-details
          clearable
          prepend-inner-icon="mdi-magnify"
          class="logs-search"
        />
        <v-spacer />
        <v-btn
          data-test="logs-refresh"
          variant="text"
          prepend-icon="mdi-refresh"
          :loading="store.loading"
          @click="store.fetch()"
        >
          更新
        </v-btn>
      </v-card-text>
    </v-card>

    <v-alert
      v-if="store.error"
      data-test="logs-error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ store.error }}
    </v-alert>

    <v-card
      variant="flat"
      border
    >
      <v-progress-linear
        :active="store.loading"
        indeterminate
        color="primary"
      />
      <v-table
        data-test="logs-table"
        density="comfortable"
      >
        <thead>
          <tr>
            <th class="text-no-wrap">
              日時
            </th>
            <th>種別</th>
            <th>実行者</th>
            <th>内容</th>
            <th class="text-no-wrap">
              詳細
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(entry, index) in store.filtered"
            :key="`${entry.at}-${entry.target}-${index}`"
            data-test="logs-row"
          >
            <td class="text-no-wrap">
              {{ formatDateTimeSeconds(entry.at) }}
            </td>
            <td>
              <v-chip
                size="small"
                :color="TARGET_COLORS[entry.target]"
              >
                {{ AUDIT_TARGET_LABELS[entry.target] }}
              </v-chip>
            </td>
            <td>{{ actorLabel(entry) }}</td>
            <td>{{ summarize(entry) }}</td>
            <td>
              <v-btn
                :data-test="`logs-detail-${index}`"
                size="small"
                variant="text"
                icon="mdi-code-json"
                :aria-label="`${formatDateTimeSeconds(entry.at)} の詳細`"
                @click="openDetail(entry)"
              />
            </td>
          </tr>
        </tbody>
      </v-table>
      <v-alert
        v-if="!store.filtered.length && !store.loading"
        data-test="logs-empty"
        type="info"
        variant="tonal"
        class="ma-4"
      >
        該当する操作ログがありません。
      </v-alert>
    </v-card>

    <p
      data-test="logs-note"
      class="text-caption text-medium-emphasis mt-3 mb-0"
    >
      記録対象は手動実行（受理されたもの）・設定変更・単価表変更です。スケジュール実行は実行履歴タブを参照してください。時刻は JST です。直近 200 件まで表示します。
    </p>

    <v-dialog
      v-model="detailDialog"
      max-width="720"
    >
      <v-card data-test="logs-detail-dialog">
        <v-card-item>
          <v-card-title class="text-subtitle-1">
            操作の詳細
          </v-card-title>
          <v-card-subtitle v-if="detail">
            {{ formatDateTimeSeconds(detail.at) }} / {{ AUDIT_TARGET_LABELS[detail.target] }} / {{ actorLabel(detail) }}
          </v-card-subtitle>
        </v-card-item>
        <v-card-text v-if="detail">
          <div class="text-subtitle-2 mb-1">
            変更前
          </div>
          <pre
            class="logs-json"
            data-test="logs-detail-before"
          >{{ toJson(detail.before) }}</pre>
          <div class="text-subtitle-2 mt-4 mb-1">
            変更後
          </div>
          <pre
            class="logs-json"
            data-test="logs-detail-after"
          >{{ toJson(detail.after) }}</pre>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn
            data-test="logs-detail-close"
            @click="detailDialog = false"
          >
            閉じる
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.logs-filter {
  max-width: 12rem;
}

.logs-search {
  max-width: 20rem;
}

/* 長い JSON でダイアログが横に伸びないようにする（UI-03） */
.logs-json {
  max-height: 18rem;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 0.8125rem;
}
</style>
