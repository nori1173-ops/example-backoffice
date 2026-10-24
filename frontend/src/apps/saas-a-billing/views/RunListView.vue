<script setup lang="ts">
import { computed, onActivated, onDeactivated, onUnmounted, ref } from 'vue'
import { DASH, formatCount, formatDate, formatDateTime, formatYen } from '@/utils/format'
import HelpTip from '@/components/HelpTip.vue'
import { billingApi } from '../api'
import EnvBadge from '../components/EnvBadge.vue'
import RunDialog from '../components/RunDialog.vue'
import StatusChip from '../components/StatusChip.vue'
import TriggerChip from '../components/TriggerChip.vue'
import { HELP } from '../help'
import { runKey, useRunsStore } from '../stores/runs'
import type { BillingSummary, RunStatus, RunSummary, SettingsView } from '../types'

const AUTO_RUN_HOUR = 6
const MAX_MONTHS_AHEAD = 24
/** API は succeeded / distributed 以外の月を 404 にするため、キーの有無ではなく状態で判定する（設計 8 章）。 */
const DOWNLOADABLE: readonly RunStatus[] = ['succeeded', 'distributed']
const DOWNLOAD_BLOCKED_REASON = '成功・配布済みの月のみ'
/** 自動実行や他の人が起こした RUN も進むように、実行中の間だけ一覧を取り直す（UI-04）。 */
const ACTIVE_STATUSES: readonly RunStatus[] = ['queued', 'running']
const POLL_INTERVAL_MS = 10_000
/** 内訳カウント列。説明は月次内訳の件数タイルと共用する（#1012）。 */
const COUNT_COLUMNS: readonly { key: keyof BillingSummary; label: string }[] = [
  { key: 'tenant_count', label: 'テナント数' },
  { key: 'device_count', label: '地点数' },
  { key: 'no_data_count', label: '通信なし' },
  { key: 'excluded_count', label: '除外' },
  { key: 'review_count', label: '要確認' },
  { key: 'failed_tenant_count', label: '取得失敗' },
  { key: 'missing_count', label: '消えた地点' },
]

const store = useRunsStore()
const settings = ref<SettingsView | null>(null)
const runDialog = ref(false)
const lastUpdated = ref<Date | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

function monthKey(date: Date): string {
  return formatDate(date).slice(0, 7)
}

/**
 * 締め日時を過ぎた最初の 06:00 JST が次回の自動実行になる（設計 3.1 / 6.2）。
 * 自動実行の開始月より前の対象月は trigger が空振りするので飛ばす。
 */
function nextAutoRunAt(config: SettingsView, now: Date): Date | null {
  if (!config.auto_run_enabled) {
    return null
  }
  const [hour, minute] = config.cutoff_time.split(':').map(Number)
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) {
    return null
  }
  for (let ahead = 0; ahead <= MAX_MONTHS_AHEAD; ahead += 1) {
    const target = new Date(now.getFullYear(), now.getMonth() + ahead, 1)
    const cutoff = new Date(
      target.getFullYear(),
      target.getMonth(),
      config.cutoff_day,
      hour,
      minute,
    )
    if (cutoff <= now || monthKey(target) < config.auto_run_from_month) {
      continue
    }
    const sameDay = new Date(
      cutoff.getFullYear(),
      cutoff.getMonth(),
      cutoff.getDate(),
      AUTO_RUN_HOUR,
    )
    return sameDay < cutoff
      ? new Date(cutoff.getFullYear(), cutoff.getMonth(), cutoff.getDate() + 1, AUTO_RUN_HOUR)
      : sameDay
  }
  return null
}

const nextAutoRunLabel = computed(() => {
  if (!settings.value) {
    return DASH
  }
  const at = nextAutoRunAt(settings.value, new Date())
  return at ? formatDateTime(at) : '自動実行は無効'
})

function canDownload(run: RunSummary): boolean {
  return DOWNLOADABLE.includes(run.status)
}

function distributionLabel(run: RunSummary): string {
  if (run.status === 'distributed') {
    return '配布済み'
  }
  return run.distribute ? '未配布' : '配布なし'
}

const activeRuns = computed(() => store.runs.filter((run) => ACTIVE_STATUSES.includes(run.status)))

function downloadKey(run: RunSummary): string {
  return runKey(run.month, run.target_env)
}

function stopPolling(): void {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

function syncPolling(): void {
  if (!activeRuns.value.length) {
    stopPolling()
    return
  }
  timer ??= setInterval(() => void reload(), POLL_INTERVAL_MS)
}

async function reload(): Promise<void> {
  await store.fetchRuns()
  lastUpdated.value = new Date()
  syncPolling()
}

/** 実行中の行は個別にも追跡する。`pollRun` は同じ月の多重起動を防いでいる。 */
async function refresh(): Promise<void> {
  await reload()
  for (const run of activeRuns.value) {
    void store.pollRun(run.month, run.target_env)
  }
}

async function loadSettings(): Promise<void> {
  try {
    settings.value = await billingApi.getSettings()
  } catch {
    settings.value = null
  }
}

// keep-alive 配下では onMounted が再発火しないため、画面に入るたびに取り直す（UI-04）
onActivated(async () => {
  await refresh()
  if (!settings.value) {
    await loadSettings()
  }
})

onDeactivated(stopPolling)
onUnmounted(stopPolling)
</script>

<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <div>
        <h1 class="text-h5 mb-1">
          実行履歴
        </h1>
        <p class="text-body-2 text-medium-emphasis mb-0">
          月額課金レポートの実行結果です。
        </p>
      </div>
      <v-spacer />
      <HelpTip :text="HELP.runList.refresh">
        <template #default="{ props }">
          <v-btn
            v-bind="props"
            data-test="refresh"
            variant="text"
            prepend-icon="mdi-refresh"
            :loading="store.loading"
            @click="refresh"
          >
            更新
          </v-btn>
        </template>
      </HelpTip>
      <HelpTip :text="HELP.runList.manualRun">
        <template #default="{ props }">
          <v-btn
            v-bind="props"
            data-test="manual-run"
            color="primary"
            prepend-icon="mdi-play-outline"
            @click="runDialog = true"
          >
            手動実行
          </v-btn>
        </template>
      </HelpTip>
    </div>

    <v-card
      variant="flat"
      border
      class="mb-4"
    >
      <v-card-text class="d-flex align-center flex-wrap ga-4">
        <div
          data-test="next-auto-run"
          class="text-body-2"
        >
          <span class="text-medium-emphasis me-2">次回自動実行予定</span>
          <span class="font-weight-medium">{{ nextAutoRunLabel }}</span>
        </div>
        <div
          data-test="last-updated"
          class="text-body-2"
        >
          <span class="text-medium-emphasis me-2">最終更新</span>
          <span class="font-weight-medium">{{ formatDateTime(lastUpdated) }}</span>
          <span
            v-if="activeRuns.length"
            class="text-caption text-medium-emphasis ms-2"
          >実行中のため 10 秒ごとに自動更新します</span>
        </div>
      </v-card-text>
    </v-card>

    <v-alert
      v-if="store.error"
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
      <v-table density="comfortable">
        <thead>
          <tr>
            <th class="text-no-wrap d-none d-lg-table-cell">
              集計対象環境
            </th>
            <th class="text-no-wrap">
              月
            </th>
            <th>状態</th>
            <th class="text-no-wrap">
              実行日時
            </th>
            <th class="text-end text-no-wrap">
              <HelpTip :text="HELP.summary.total_amount">
                <template #default="{ props }">
                  <span v-bind="props">合計金額</span>
                </template>
              </HelpTip>
            </th>
            <th class="text-no-wrap">
              <HelpTip :text="HELP.runList.distribution">
                <template #default="{ props }">
                  <span v-bind="props">配布状況</span>
                </template>
              </HelpTip>
            </th>
            <th class="text-no-wrap">
              ダウンロード
            </th>
            <th class="text-end text-no-wrap d-none d-xl-table-cell">
              <HelpTip :text="HELP.runList.attempt">
                <template #default="{ props }">
                  <span v-bind="props">試行回数</span>
                </template>
              </HelpTip>
            </th>
            <th
              v-for="column in COUNT_COLUMNS"
              :key="column.key"
              class="text-end text-no-wrap d-none d-xl-table-cell"
            >
              <HelpTip :text="HELP.summary[column.key]">
                <template #default="{ props }">
                  <span v-bind="props">{{ column.label }}</span>
                </template>
              </HelpTip>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!store.runs.length && !store.loading">
            <td
              colspan="15"
              class="text-center text-medium-emphasis py-8"
            >
              実行履歴がありません。
            </td>
          </tr>
          <tr
            v-for="run in store.runs"
            :key="`${run.target_env}-${run.month}`"
          >
            <td class="text-no-wrap d-none d-lg-table-cell">
              <EnvBadge :target-env="run.target_env" />
            </td>
            <td class="text-no-wrap">
              <router-link
                :to="{
                  name: 'saas-a-billing.run-detail',
                  params: { targetEnv: run.target_env, month: run.month },
                }"
              >
                {{ run.month }}
              </router-link>
              <TriggerChip
                :trigger="run.trigger"
                :data-test="`trigger-${run.target_env}-${run.month}`"
                class="ml-2"
              />
            </td>
            <td>
              <StatusChip :status="run.status" />
              <div
                v-if="run.error"
                class="text-caption text-error run-error"
              >
                {{ run.error }}
              </div>
            </td>
            <td class="text-no-wrap">
              {{ formatDateTime(run.started_at) }}
            </td>
            <td
              data-test="run-total-amount"
              class="text-end text-no-wrap font-weight-bold"
            >
              {{ formatYen(run.summary?.total_amount) }}
            </td>
            <td class="text-no-wrap">
              {{ distributionLabel(run) }}
            </td>
            <td>
              <HelpTip :text="HELP.download">
                <template #default="{ props }">
                  <v-btn
                    v-bind="props"
                    :data-test="`download-${run.target_env}-${run.month}`"
                    :disabled="!canDownload(run)"
                    :loading="store.downloading === downloadKey(run)"
                    size="small"
                    variant="text"
                    prepend-icon="mdi-file-excel-outline"
                    @click="store.download(run.month, run.target_env)"
                  >
                    Excel
                  </v-btn>
                </template>
              </HelpTip>
              <div
                v-if="!canDownload(run)"
                :data-test="`download-reason-${run.target_env}-${run.month}`"
                class="text-caption text-medium-emphasis download-reason"
              >
                {{ DOWNLOAD_BLOCKED_REASON }}
              </div>
            </td>
            <td class="text-end d-none d-xl-table-cell">
              {{ run.attempt }}
            </td>
            <td
              v-for="column in COUNT_COLUMNS"
              :key="column.key"
              class="text-end d-none d-xl-table-cell"
            >
              {{ formatCount(run.summary?.[column.key]) }}
            </td>
          </tr>
        </tbody>
      </v-table>
      <div
        data-test="table-scroll-hint"
        class="text-caption text-medium-emphasis px-4 py-2"
      >
        画面が狭いときは集計対象環境と内訳カウント（試行回数〜消えた地点）を隠しています。表は横にスクロールできます。
      </div>
    </v-card>

    <RunDialog
      v-model="runDialog"
      :settings="settings"
    />
  </div>
</template>

<style scoped>
/* 任意長のエラー文で表全体が横に伸びるのを防ぐ（UI-03） */
.run-error {
  max-width: 8rem;
  white-space: normal;
}

.download-reason {
  max-width: 6rem;
  white-space: normal;
}
</style>
