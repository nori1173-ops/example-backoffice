<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { billingApi } from '../api'
import EnvBadge from '../components/EnvBadge.vue'
import RunDialog from '../components/RunDialog.vue'
import StatusChip from '../components/StatusChip.vue'
import { useRunsStore } from '../stores/runs'
import type { RunStatus, RunSummary, SettingsView, TargetEnv } from '../types'

type EnvFilter = TargetEnv | 'all'

const DASH = '—'
const AUTO_RUN_HOUR = 6
const MAX_MONTHS_AHEAD = 24
/** API は succeeded / distributed 以外の月を 404 にするため、キーの有無ではなく状態で判定する（設計 8 章）。 */
const DOWNLOADABLE: readonly RunStatus[] = ['succeeded', 'distributed']

const store = useRunsStore()
const settings = ref<SettingsView | null>(null)
const envFilter = ref<EnvFilter>(store.targetEnvFilter ?? 'all')
const runDialog = ref(false)

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** API は JST オフセット付きの ISO 8601 を返すので、表示は文字列のまま組み替える。 */
function formatDateTime(iso: string | undefined): string {
  if (!iso) {
    return DASH
  }
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso)
  return parts ? `${parts[1]}/${parts[2]}/${parts[3]} ${parts[4]}:${parts[5]}` : iso
}

function formatDate(date: Date): string {
  return `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function formatCount(value: number | undefined): string {
  return value === undefined ? DASH : value.toLocaleString('ja-JP')
}

function formatYen(value: number | undefined): string {
  return value === undefined ? DASH : `¥${value.toLocaleString('ja-JP')}`
}

function monthKey(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}`
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
  return at ? formatDate(at) : '自動実行は無効'
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

function reload(filter: EnvFilter): Promise<void> {
  return store.fetchRuns(filter === 'all' ? undefined : filter)
}

watch(envFilter, (value) => {
  void reload(value)
})

onMounted(async () => {
  await reload(envFilter.value)
  try {
    settings.value = await billingApi.getSettings()
  } catch {
    settings.value = null
  }
})
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
      <v-btn
        color="primary"
        prepend-icon="mdi-play-outline"
        @click="runDialog = true"
      >
        手動実行
      </v-btn>
    </div>

    <v-card
      variant="flat"
      border
      class="mb-4"
    >
      <v-card-text class="d-flex align-center flex-wrap ga-4">
        <v-btn-toggle
          v-model="envFilter"
          data-test="env-filter"
          mandatory
          density="comfortable"
          variant="outlined"
          divided
        >
          <v-btn value="all">
            すべて
          </v-btn>
          <v-btn value="verification">
            検証環境
          </v-btn>
          <v-btn value="production">
            本番
          </v-btn>
        </v-btn-toggle>
        <div
          data-test="next-auto-run"
          class="text-body-2"
        >
          <span class="text-medium-emphasis me-2">次回自動実行予定</span>
          <span class="font-weight-medium">{{ nextAutoRunLabel }}</span>
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
      <v-table
        density="comfortable"
        class="text-no-wrap"
      >
        <thead>
          <tr>
            <th>集計対象環境</th>
            <th>月</th>
            <th>状態</th>
            <th>実行日時</th>
            <th class="text-end">
              試行回数
            </th>
            <th class="text-end">
              テナント数
            </th>
            <th class="text-end">
              地点数
            </th>
            <th class="text-end">
              通信なし
            </th>
            <th class="text-end">
              除外
            </th>
            <th class="text-end">
              要確認
            </th>
            <th class="text-end">
              取得失敗
            </th>
            <th class="text-end">
              消えた地点
            </th>
            <th class="text-end">
              合計金額
            </th>
            <th>配布状況</th>
            <th>ダウンロード</th>
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
            <td>
              <EnvBadge :target-env="run.target_env" />
            </td>
            <td>
              <router-link
                :to="{
                  name: 'saas-a-billing.run-detail',
                  params: { targetEnv: run.target_env, month: run.month },
                }"
              >
                {{ run.month }}
              </router-link>
            </td>
            <td>
              <StatusChip :status="run.status" />
              <div
                v-if="run.error"
                class="text-caption text-error"
              >
                {{ run.error }}
              </div>
            </td>
            <td>{{ formatDateTime(run.started_at) }}</td>
            <td class="text-end">
              {{ run.attempt }}
            </td>
            <td class="text-end">
              {{ formatCount(run.summary?.tenant_count) }}
            </td>
            <td class="text-end">
              {{ formatCount(run.summary?.device_count) }}
            </td>
            <td class="text-end">
              {{ formatCount(run.summary?.no_data_count) }}
            </td>
            <td class="text-end">
              {{ formatCount(run.summary?.excluded_count) }}
            </td>
            <td class="text-end">
              {{ formatCount(run.summary?.review_count) }}
            </td>
            <td class="text-end">
              {{ formatCount(run.summary?.failed_tenant_count) }}
            </td>
            <td class="text-end">
              {{ formatCount(run.summary?.missing_count) }}
            </td>
            <td class="text-end">
              {{ formatYen(run.summary?.total_amount) }}
            </td>
            <td>{{ distributionLabel(run) }}</td>
            <td>
              <v-btn
                :data-test="`download-${run.target_env}-${run.month}`"
                :disabled="!canDownload(run)"
                size="small"
                variant="text"
                prepend-icon="mdi-file-excel-outline"
                @click="store.download(run.month, run.target_env)"
              >
                Excel
              </v-btn>
            </td>
          </tr>
        </tbody>
      </v-table>
    </v-card>

    <RunDialog
      v-model="runDialog"
      :settings="settings"
    />
  </div>
</template>
