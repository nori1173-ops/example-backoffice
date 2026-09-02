<script setup lang="ts">
import { computed, onActivated, onDeactivated, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useDisplay } from 'vuetify'
import {
  DASH,
  formatCount,
  formatDateTime,
  formatDateTimeSeconds,
  formatYen,
  toWallClock,
} from '@/utils/format'
import { billingApi } from '../api'
import EnvBadge from '../components/EnvBadge.vue'
import JudgementChip, {
  JUDGEMENT_FILLS,
  JUDGEMENT_LABELS,
  MISSING_TEXT_COLOR,
  REVIEW_REASONS,
  type ChipJudgement,
} from '../components/JudgementChip.vue'
import RunConfirmSummary from '../components/RunConfirmSummary.vue'
import StatusChip from '../components/StatusChip.vue'
import { runKey, useRunsStore } from '../stores/runs'
import type {
  BillingSummary,
  ClosingPeriod,
  DeviceJudgement,
  Judgement,
  RunDetail,
  RunStatus,
  SettingsView,
  TargetEnv,
  TenantResult,
} from '../types'

interface GroupNode {
  name: string
  devices: DeviceJudgement[]
}

interface TenantNode {
  tenant: TenantResult
  groups: GroupNode[]
}

const NO_GROUP = '(現場なし)'
/** billing.py の BOUNDARY_NOTE。備考の印であって判定・金額には影響しない（設計 2.5 / 6.5）。 */
const BOUNDARY_NOTE = '境界日のみ受信'
/**
 * API は succeeded / distributed 以外の月を 404 にする（設計 8 章）。
 * force 無しの再実行も同じ状態で 409 ALREADY_SUCCEEDED になる。
 */
const FINISHED_STATUSES: readonly RunStatus[] = ['succeeded', 'distributed']
/** 実行中の月は結果が変わるので取り直す（UI-04 と同じ間隔）。 */
const ACTIVE_STATUSES: readonly RunStatus[] = ['queued', 'running']
const POLL_INTERVAL_MS = 10_000

/** 合計金額は上部の大きな表示に出すため、ここでは件数だけを並べる。 */
const SUMMARY_ITEMS: readonly { key: keyof BillingSummary; label: string }[] = [
  { key: 'tenant_count', label: 'テナント数' },
  { key: 'device_count', label: '地点数' },
  { key: 'billable_count', label: '課金' },
  { key: 'no_data_count', label: '通信なし' },
  { key: 'excluded_count', label: '除外' },
  { key: 'review_count', label: '要確認' },
  { key: 'failed_tenant_count', label: '取得失敗' },
  { key: 'missing_count', label: '消えた地点' },
]

/** 金額が出せない理由は状態ごとに違うので、— の隣に必ず添える。 */
const TOTAL_NOTES: Partial<Record<RunStatus, string>> = {
  queued: '実行の順番待ちです。終わると合計金額が出ます。',
  running: '集計中です。終わると合計金額が出ます。',
  failed: '集計に失敗したため合計金額がありません。再実行してください。',
}

const JUDGEMENT_FILTERS: readonly { value: Judgement; label: string }[] = [
  { value: 'billable', label: '課金' },
  { value: 'no_data', label: '通信なし' },
  { value: 'excluded', label: '除外' },
  { value: 'failed', label: '取得失敗' },
]

const CAMERA_SOURCE_LABELS: Record<string, string> = { export: 'export', settings: '設定' }

/** 行の塗り分けは Excel と同じ意味を持つので、色だけに頼らせない（UI-20 / 設計 6.6）。 */
const LEGEND: readonly { judgement: ChipJudgement; description: string }[] = [
  { judgement: 'billable', description: '締め期間内に受信があり課金対象' },
  { judgement: 'no_data', description: '締め期間内に受信が無い（0 円）' },
  { judgement: 'excluded', description: '除外キーワードに一致（0 円）' },
  { judgement: 'failed', description: 'テナントの取得に失敗（0 円・再実行が要る）' },
  { judgement: 'missing', description: '前回 RUN にあり今回の一覧に無い（金額に計上しない）' },
]

const REVIEW_HELP = Object.values(REVIEW_REASONS)

/** 設計 6.6 の保存期間の色。モノクロ印刷でも文字で判別できるよう表示も変える。 */
const RETENTION_COLORS: Record<string, { fill: string; text: string }> = {
  '3': { fill: '#C6EFCE', text: '#006100' },
  '1': { fill: '#E4DFEC', text: '#3F3151' },
  unknown: { fill: 'transparent', text: '#C00000' },
}

const route = useRoute()
const store = useRunsStore()
const { smAndDown } = useDisplay()

const detail = ref<RunDetail | null>(null)
const settings = ref<SettingsView | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const judgementFilter = ref<Judgement[]>([])
const reviewOnly = ref(false)
const boundaryOnly = ref(false)
const rerunDialog = ref(false)
const rerunDistribute = ref(false)
const rerunForce = ref(false)
const distributeAck = ref(false)
const rerunError = ref<string | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

const month = computed(() => String(route.params.month ?? ''))
const targetEnv = computed(() => String(route.params.targetEnv ?? '') as TargetEnv)
const run = computed(() => detail.value?.run ?? null)
const result = computed(() => detail.value?.result ?? null)

/** 半開区間 `[from, to)` の終端から 1 秒引いた表示にする（設計 7.3）。 */
function periodLabel(period: ClosingPeriod): string {
  const from = toWallClock(period.from)
  const to = toWallClock(period.to)
  if (!from || !to) {
    return DASH
  }
  to.setSeconds(to.getSeconds() - 1)
  return `${formatDateTimeSeconds(from)} 〜 ${formatDateTimeSeconds(to)}`
}

function retentionLabel(retention: number | null): string {
  return retention === 1 || retention === 3 ? `${retention}年` : '不明(3年扱い)'
}

function retentionStyle(retention: number | null): Record<string, string> {
  const colors = RETENTION_COLORS[String(retention)] ?? RETENTION_COLORS.unknown
  return { '--badge-fill': colors.fill, '--badge-text': colors.text }
}

function cameraLabel(device: DeviceJudgement): string {
  return device.camera_source ? CAMERA_SOURCE_LABELS[device.camera_source] : DASH
}

function rowStyle(device: DeviceJudgement): Record<string, string> {
  return { '--row-fill': JUDGEMENT_FILLS[device.judgement] }
}

function legendStyle(judgement: ChipJudgement): Record<string, string> {
  return {
    '--swatch-fill': JUDGEMENT_FILLS[judgement],
    '--swatch-text': judgement === 'missing' ? MISSING_TEXT_COLOR : 'inherit',
  }
}

const missingRowStyle = { '--row-text': MISSING_TEXT_COLOR }

/** 要確認は判定を問わず立つ（除外・通信なしの行にも理由が付きうる）。 */
function matches(device: DeviceJudgement): boolean {
  if (judgementFilter.value.length && !judgementFilter.value.includes(device.judgement)) {
    return false
  }
  if (reviewOnly.value && !device.flags.length) {
    return false
  }
  return !boundaryOnly.value || device.note.includes(BOUNDARY_NOTE)
}

/** 地点は `group_cd` → `device_cd` 昇順で届くため、現場は出現順にまとめる（設計 6.5）。 */
function groupDevices(devices: DeviceJudgement[]): GroupNode[] {
  const nodes: GroupNode[] = []
  for (const device of devices) {
    const name = device.group_names[0] ?? NO_GROUP
    const last = nodes[nodes.length - 1]
    if (last && last.name === name) {
      last.devices.push(device)
    } else {
      nodes.push({ name, devices: [device] })
    }
  }
  return nodes
}

const tree = computed<TenantNode[]>(() =>
  (result.value?.tenants ?? [])
    .map((tenant) => ({ tenant, groups: groupDevices(tenant.devices.filter(matches)) }))
    .filter((node) => node.groups.length > 0),
)

/** 明細が無い月でも RUN の集計値だけは出せる（設計 7.1）。 */
const totalAmount = computed(
  () => result.value?.summary.total_amount ?? run.value?.summary?.total_amount ?? null,
)

const totalNote = computed(() => {
  if (totalAmount.value !== null) {
    return null
  }
  if (!run.value) {
    return 'この月はまだ実行されていません。'
  }
  return TOTAL_NOTES[run.value.status] ?? 'この月の合計金額はまだ確定していません。'
})

/** 絞り込み中の合計行は表示中の行の合計ではないため、注記を添える。 */
const filtered = computed(
  () => judgementFilter.value.length > 0 || reviewOnly.value || boundaryOnly.value,
)

const canDownload = computed(() => !!run.value && FINISHED_STATUSES.includes(run.value.status))

/** 実行の失敗はダイアログの中で伝えるので、上部の帯は詳細取得の失敗に絞る。 */
const pageError = computed(() => error.value ?? (rerunError.value ? null : store.error))
const downloading = computed(() => store.downloading === runKey(month.value, targetEnv.value))

const distributeTargets = computed(() => {
  const labels = []
  if (settings.value?.mail_enabled) {
    labels.push('メール')
  }
  if (settings.value?.slack_enabled) {
    labels.push('Slack')
  }
  return labels.length ? `配布先: ${labels.join('・')}` : '配布先: なし（設定で配布が無効）'
})

function stopPolling(): void {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

/** 実行中・待機中の月だけ取り直す。終わったら止める（UI-04）。 */
function syncPolling(): void {
  if (run.value && ACTIVE_STATUSES.includes(run.value.status)) {
    timer ??= setInterval(() => void load(true), POLL_INTERVAL_MS)
  } else {
    stopPolling()
  }
}

/**
 * 切り替え中に前の月の数字を残さない（UI-01）。
 * `quiet` は実行中の自動更新用で、進行表示を出さず前回の内容も消さない。
 */
async function load(quiet = false): Promise<void> {
  if (!quiet) {
    loading.value = true
    error.value = null
    detail.value = null
  }
  try {
    detail.value = await billingApi.getRun(month.value, {
      targetEnv: targetEnv.value,
      detail: true,
    })
  } catch {
    if (!quiet) {
      error.value = '月次内訳の取得に失敗しました'
      detail.value = null
    }
  } finally {
    if (!quiet) {
      loading.value = false
    }
    syncPolling()
  }
}

async function loadSettings(): Promise<void> {
  try {
    settings.value = await billingApi.getSettings()
  } catch {
    settings.value = null
  }
}

/** 実行中・待機中の月に重ねると二重実行になる（UI-07）。 */
const rerunBlocked = computed(() => !!run.value && ACTIVE_STATUSES.includes(run.value.status))

/** 成功済みの月は force 無しだと API が 409 を返すため、開いた時点で伝える。 */
const forceRequired = computed(() => !!run.value && FINISHED_STATUSES.includes(run.value.status))

// 配布は実際の宛先を読んだ確認を挟む（設計 R6 / UI-07）
const rerunConfirmed = computed(() => !rerunDistribute.value || distributeAck.value)
const rerunDangerous = computed(
  () => settings.value?.target_env === 'production' && rerunDistribute.value,
)

function openRerun(): void {
  rerunDistribute.value = false
  rerunForce.value = false
  distributeAck.value = false
  rerunError.value = null
  rerunDialog.value = true
}

/**
 * 集計対象環境はサイト固定なので送らない（設計 R1）。
 * 受け付けられたら即座に取り直し、実行中の間は自動更新に任せる。
 */
async function rerun(): Promise<void> {
  rerunError.value = null
  const accepted = await store.createRun({
    month: month.value,
    distribute: rerunDistribute.value,
    force: rerunForce.value,
  })
  if (!accepted) {
    rerunError.value = store.error ?? '実行の開始に失敗しました'
    return
  }
  rerunDialog.value = false
  await load()
}

// keep-alive 配下では onMounted が再発火せず、別の月の内訳が残っていた（UI-01）
onActivated(async () => {
  await Promise.all([load(), settings.value ? Promise.resolve() : loadSettings()])
})

onDeactivated(stopPolling)
onUnmounted(stopPolling)

// 配列で監視すると同値でも毎回発火して二重に取りに行くため、文字列で比べる
watch(
  () => `${String(route.params.targetEnv ?? '')}/${String(route.params.month ?? '')}`,
  () => {
    if (route.name === 'saas-a-billing.run-detail') {
      void load()
    }
  },
)
</script>

<template>
  <div>
    <v-btn
      data-test="back-to-runs"
      variant="text"
      size="small"
      prepend-icon="mdi-arrow-left"
      :to="{ name: 'saas-a-billing.runs' }"
      class="mb-2"
    >
      実行履歴へ戻る
    </v-btn>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <div>
        <h1 class="text-h5 mb-1">
          月次内訳 {{ month }}
        </h1>
        <div class="d-flex align-center ga-2">
          <EnvBadge :target-env="targetEnv" />
          <StatusChip
            v-if="run"
            :status="run.status"
          />
          <span
            v-if="run?.saasa_host"
            class="text-body-2 text-medium-emphasis"
          >{{ run.saasa_host }}</span>
        </div>
      </div>
      <v-spacer />
      <v-btn
        data-test="download"
        :disabled="!canDownload"
        :loading="downloading"
        variant="text"
        prepend-icon="mdi-file-excel-outline"
        @click="store.download(month, targetEnv)"
      >
        Excel
      </v-btn>
      <span
        v-if="!canDownload"
        data-test="download-reason"
        class="text-caption text-medium-emphasis"
      >成功・配布済みの月だけダウンロードできます</span>
      <v-btn
        data-test="rerun"
        color="primary"
        prepend-icon="mdi-refresh"
        :disabled="rerunBlocked"
        @click="openRerun"
      >
        再実行
      </v-btn>
      <span
        v-if="rerunBlocked"
        data-test="rerun-blocked-reason"
        class="text-caption text-medium-emphasis"
      >実行中のため再実行できません</span>
    </div>

    <v-progress-linear
      :active="loading"
      indeterminate
      color="primary"
    />

    <v-alert
      v-if="pageError"
      data-test="page-error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ pageError }}
    </v-alert>

    <v-card
      v-if="!loading"
      data-test="total-amount-card"
      variant="flat"
      border
      class="mb-4"
    >
      <v-card-text class="d-flex align-center flex-wrap ga-4">
        <div>
          <div class="text-caption text-medium-emphasis">
            合計金額
          </div>
          <div
            data-test="total-amount"
            class="total-amount"
            :class="{ 'total-amount--compact': smAndDown }"
          >
            {{ formatYen(totalAmount) }}
          </div>
        </div>
        <EnvBadge :target-env="targetEnv" />
        <span
          v-if="totalNote"
          data-test="total-amount-note"
          class="text-body-2 text-medium-emphasis"
        >{{ totalNote }}</span>
      </v-card-text>
    </v-card>

    <v-alert
      v-if="!loading && !result"
      type="info"
      variant="tonal"
      class="mb-4"
    >
      この月の内訳はまだ生成されていません。
    </v-alert>

    <template v-if="result">
      <v-card
        variant="flat"
        border
        class="mb-4"
      >
        <v-card-text>
          <div
            data-test="summary"
            class="d-flex flex-wrap ga-6"
          >
            <div
              v-for="item in SUMMARY_ITEMS"
              :key="item.key"
              data-test="summary-item"
            >
              <div class="text-caption text-medium-emphasis">
                {{ item.label }}
              </div>
              <div class="text-h6">
                {{ formatCount(result.summary[item.key]) }}
              </div>
            </div>
          </div>
          <v-divider class="my-4" />
          <div
            data-test="closing-period"
            class="text-body-2"
          >
            <span class="text-medium-emphasis me-2">課金対象期間</span>
            {{ periodLabel(result.closing_period) }}
            <span class="text-medium-emphasis ms-2">
              （締め日時: 毎月 {{ result.closing_period.cutoff_day }} 日
              {{ result.closing_period.cutoff_time }}・受信日時基準・JST）
            </span>
          </div>
        </v-card-text>
      </v-card>

      <v-card
        variant="flat"
        border
        class="mb-4"
      >
        <v-card-text class="d-flex align-center flex-wrap ga-4">
          <v-btn-toggle
            v-model="judgementFilter"
            data-test="judgement-filter"
            multiple
            density="comfortable"
            variant="outlined"
            divided
          >
            <v-btn
              v-for="filter in JUDGEMENT_FILTERS"
              :key="filter.value"
              :value="filter.value"
            >
              {{ filter.label }}
            </v-btn>
          </v-btn-toggle>
          <v-checkbox
            v-model="reviewOnly"
            data-test="review-only"
            label="要確認のみ"
            density="compact"
            hide-details
          />
          <v-checkbox
            v-model="boundaryOnly"
            data-test="boundary-only"
            label="境界日のみ受信"
            density="compact"
            hide-details
          />
        </v-card-text>
      </v-card>

      <v-card
        data-test="tree"
        variant="flat"
        border
        class="mb-4"
      >
        <v-card-item>
          <v-card-title class="text-subtitle-1">
            地点一覧
          </v-card-title>
        </v-card-item>
        <div
          data-test="judgement-legend"
          class="d-flex flex-wrap ga-4 px-4"
        >
          <span
            v-for="item in LEGEND"
            :key="item.judgement"
            class="d-inline-flex align-center ga-2 text-caption"
          >
            <span
              class="legend-swatch"
              :style="legendStyle(item.judgement)"
            />
            {{ JUDGEMENT_LABELS[item.judgement] }}
          </span>
        </div>
        <v-expansion-panels
          variant="accordion"
          class="px-4 py-2"
        >
          <v-expansion-panel
            eager
            elevation="0"
          >
            <v-expansion-panel-title class="text-body-2">
              判定と「要確認」の意味
            </v-expansion-panel-title>
            <v-expansion-panel-text data-test="judgement-help">
              <ul class="text-body-2 ps-5 mb-2">
                <li
                  v-for="item in LEGEND"
                  :key="item.judgement"
                >
                  {{ JUDGEMENT_LABELS[item.judgement] }}: {{ item.description }}
                </li>
              </ul>
              <div class="text-body-2 font-weight-medium">
                要確認
              </div>
              <ul class="text-body-2 ps-5 mb-0">
                <li
                  v-for="reason in REVIEW_HELP"
                  :key="reason"
                >
                  {{ reason }}
                </li>
              </ul>
            </v-expansion-panel-text>
          </v-expansion-panel>
        </v-expansion-panels>
        <v-table density="comfortable">
          <thead>
            <tr>
              <th class="text-no-wrap">
                地点CD
              </th>
              <th>地点名</th>
              <th class="d-none d-lg-table-cell">
                フォーマット
              </th>
              <th class="text-end d-none d-lg-table-cell">
                項目数
              </th>
              <th class="d-none d-lg-table-cell">
                カメラ
              </th>
              <th class="text-no-wrap">
                最終受信日時
              </th>
              <th>判定</th>
              <th class="text-end text-no-wrap">
                単価
              </th>
              <th class="text-end text-no-wrap">
                金額
              </th>
              <th>備考</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!tree.length">
              <td
                colspan="10"
                class="text-center text-medium-emphasis py-8"
              >
                条件に合う地点がありません。
              </td>
            </tr>
            <template
              v-for="node in tree"
              :key="node.tenant.tenant_cd"
            >
              <tr data-test="tenant-row">
                <td
                  colspan="10"
                  class="bg-grey-lighten-4"
                >
                  <span class="font-weight-bold">
                    ■ {{ node.tenant.tenant_cd }} {{ node.tenant.tenant_name }}
                  </span>
                  <span
                    class="retention-badge ms-2"
                    :style="retentionStyle(node.tenant.retention_period)"
                  >{{ retentionLabel(node.tenant.retention_period) }}</span>
                  <span class="ms-2 text-body-2">
                    課金 {{ formatCount(node.tenant.counts.billable) }} 地点 ｜ 小計
                    <span class="font-weight-bold">{{ formatYen(node.tenant.subtotal) }}</span>
                  </span>
                  <span
                    v-if="node.tenant.status === 'failed'"
                    class="ms-2 text-body-2 text-error"
                  >取得失敗: {{ node.tenant.error }}</span>
                </td>
              </tr>
              <template
                v-for="group in node.groups"
                :key="`${node.tenant.tenant_cd}-${group.name}`"
              >
                <tr data-test="group-row">
                  <td
                    colspan="10"
                    class="ps-8 text-body-2 font-weight-medium"
                  >
                    {{ group.name }}
                  </td>
                </tr>
                <tr
                  v-for="device in group.devices"
                  :key="device.device_cd"
                  data-test="device-row"
                  class="device-row"
                  :style="rowStyle(device)"
                >
                  <td class="ps-8 text-no-wrap">
                    {{ device.device_cd }}
                  </td>
                  <td class="device-name">
                    {{ device.device_name }}
                  </td>
                  <td class="d-none d-lg-table-cell">
                    {{ device.format_name }}
                  </td>
                  <td class="text-end d-none d-lg-table-cell">
                    {{ formatCount(device.item_count) }}
                  </td>
                  <td class="d-none d-lg-table-cell">
                    {{ cameraLabel(device) }}
                  </td>
                  <td class="text-no-wrap">
                    {{ formatDateTime(device.last_received_at) }}
                  </td>
                  <td>
                    <JudgementChip
                      :judgement="device.judgement"
                      :flags="device.flags"
                    />
                  </td>
                  <td class="text-end text-no-wrap">
                    {{ formatYen(device.unit_price) }}
                  </td>
                  <td class="text-end text-no-wrap">
                    {{ formatYen(device.amount) }}
                  </td>
                  <td class="device-note">
                    {{ [device.device_description, device.note].filter(Boolean).join(' / ') }}
                  </td>
                </tr>
              </template>
            </template>
          </tbody>
          <tfoot>
            <tr
              data-test="total-row"
              class="total-row"
            >
              <td
                colspan="2"
                class="text-caption text-medium-emphasis"
              >
                <span v-if="filtered">絞り込み中も全地点の合計です</span>
              </td>
              <td
                colspan="3"
                class="d-none d-lg-table-cell"
              />
              <td />
              <td />
              <td class="text-end text-no-wrap font-weight-bold">
                合計金額
              </td>
              <td class="text-end text-no-wrap font-weight-bold">
                {{ formatYen(result.summary.total_amount) }}
              </td>
              <td />
            </tr>
          </tfoot>
        </v-table>
      </v-card>

      <v-card
        v-if="result.missing_devices.length"
        data-test="missing-devices"
        variant="flat"
        border
        class="mb-4"
      >
        <v-card-item>
          <v-card-title class="text-subtitle-1">
            消えた地点
          </v-card-title>
          <v-card-subtitle>
            前回 RUN にあり今回の地点一覧に無い地点です。金額には計上していません。
          </v-card-subtitle>
        </v-card-item>
        <v-table density="comfortable">
          <thead>
            <tr>
              <th class="text-no-wrap">
                テナントCD
              </th>
              <th>テナント名</th>
              <th>現場</th>
              <th>地点CD</th>
              <th>地点名</th>
              <th>最終確認月</th>
              <th>判定</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="device in result.missing_devices"
              :key="`${device.tenant_cd}-${device.device_cd}`"
              data-test="missing-row"
              class="missing-row"
              :style="missingRowStyle"
            >
              <td class="text-no-wrap">
                {{ device.tenant_cd }}
              </td>
              <td>{{ device.tenant_name }}</td>
              <td>{{ device.group_names.join('、') }}</td>
              <td class="text-no-wrap">
                {{ device.device_cd }}
              </td>
              <td>{{ device.device_name }}</td>
              <td>{{ device.last_seen_month }}</td>
              <td>
                <JudgementChip judgement="missing" />
              </td>
            </tr>
          </tbody>
        </v-table>
      </v-card>

      <v-card
        data-test="price-table"
        variant="flat"
        border
      >
        <v-card-item>
          <v-card-title class="text-subtitle-1">
            使用単価表
          </v-card-title>
          <v-card-subtitle>
            {{ result.price_table.effective_from }} 適用版
            <span v-if="result.price_table.note">（{{ result.price_table.note }}）</span>
          </v-card-subtitle>
        </v-card-item>
        <v-table density="comfortable">
          <thead>
            <tr>
              <th>項目数帯</th>
              <th class="text-end">
                3年保存
              </th>
              <th class="text-end">
                1年保存
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="band in result.price_table.bands"
              :key="band.max_items"
            >
              <td>〜{{ formatCount(band.max_items) }}</td>
              <td class="text-end">
                {{ formatYen(band.price_3y) }}
              </td>
              <td class="text-end">
                {{ formatYen(band.price_1y) }}
              </td>
            </tr>
            <tr>
              <td>カメラ地点</td>
              <td class="text-end">
                {{ formatYen(result.price_table.camera.price_3y) }}
              </td>
              <td class="text-end">
                {{ formatYen(result.price_table.camera.price_1y) }}
              </td>
            </tr>
          </tbody>
        </v-table>
      </v-card>
    </template>

    <v-dialog
      v-model="rerunDialog"
      max-width="480"
    >
      <v-card data-test="rerun-dialog">
        <v-card-item>
          <v-card-title>再実行の確認</v-card-title>
        </v-card-item>
        <v-card-text class="text-body-2">
          <v-alert
            data-test="rerun-overwrite-warning"
            type="warning"
            variant="tonal"
            density="compact"
            class="mb-3"
          >
            {{ month }} を集計し直し、既存の結果（Excel・明細）を上書きします。
          </v-alert>
          <v-alert
            v-if="forceRequired && !rerunForce"
            data-test="rerun-force-required"
            type="info"
            variant="tonal"
            density="compact"
            class="mb-3"
          >
            この月は既に集計済みです。結果を作り直すには「強制再実行」にチェックを入れてください。
          </v-alert>
          <v-checkbox
            v-model="rerunDistribute"
            data-test="rerun-distribute"
            label="メール・Slack へ配布する"
            density="compact"
            hide-details
          />
          <div
            data-test="distribute-targets"
            class="text-caption text-medium-emphasis ms-10 mb-2"
          >
            {{ distributeTargets }}
          </div>
          <v-checkbox
            v-model="rerunForce"
            data-test="rerun-force"
            label="強制再実行（二重起動の防止を外す）"
            density="compact"
            hide-details
          />
          <v-alert
            v-if="rerunForce"
            data-test="rerun-force-warning"
            type="warning"
            variant="tonal"
            density="compact"
            class="mt-2 mb-3"
          >
            強制再実行は二重起動の防止を外します。実行中の月に使うと二重実行になることがあります。
          </v-alert>
          <v-divider class="my-3" />
          <RunConfirmSummary
            :settings="settings"
            :month="month"
            :distribute="rerunDistribute"
          />
          <v-checkbox
            v-if="rerunDistribute"
            v-model="distributeAck"
            data-test="distribute-ack"
            label="上の配布先へ送ることを確認しました"
            density="compact"
            hide-details
            class="mt-2"
          />
          <v-alert
            v-if="rerunError"
            data-test="rerun-error"
            type="error"
            variant="tonal"
            density="compact"
            class="mt-3"
          >
            {{ rerunError }}
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn
            data-test="rerun-cancel"
            @click="rerunDialog = false"
          >
            キャンセル
          </v-btn>
          <v-btn
            data-test="rerun-confirm"
            :color="rerunDangerous ? 'error' : 'primary'"
            variant="flat"
            :disabled="!rerunConfirmed"
            @click="rerun"
          >
            {{ rerunDangerous ? '本番へ配布して実行' : '実行' }}
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
/* Vuetify 4 には text-h3 等のタイポグラフィ用ユーティリティが無く、大きさは自前で持つ */
.total-amount {
  font-size: 2.5rem;
  line-height: 1.2;
  font-weight: 700;
  color: rgb(var(--v-theme-primary));
}

.total-amount--compact {
  font-size: 1.75rem;
}

.device-row {
  background-color: var(--row-fill);
}

.missing-row {
  color: var(--row-text);
}

/* 長い名前・備考で表全体が横に伸びるのを防ぐ（UI-03） */
.device-name {
  max-width: 22rem;
  white-space: normal;
}

.device-note {
  max-width: 20rem;
  white-space: normal;
}

.total-row td {
  border-top: 2px solid rgba(0, 0, 0, 0.24);
}

.legend-swatch {
  display: inline-block;
  width: 1.25rem;
  height: 0.9rem;
  background-color: var(--swatch-fill);
  border: 1px solid rgba(0, 0, 0, 0.24);
  border-radius: 2px;
}

.retention-badge {
  background-color: var(--badge-fill);
  color: var(--badge-text);
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 4px;
  padding: 0 6px;
  font-size: 0.75rem;
  white-space: nowrap;
}
</style>
