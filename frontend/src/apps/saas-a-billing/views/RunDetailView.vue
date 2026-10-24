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
import HelpTip from '@/components/HelpTip.vue'
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
import TriggerChip from '../components/TriggerChip.vue'
import { HELP } from '../help'
import { runKey, useRunsStore } from '../stores/runs'
import { DEFAULT_EXCLUDE_KEYWORDS, isTenantExcluded } from '../utils/tenants'
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

/** 行の塗り分けは Excel と同じ意味を持つので、色だけに頼らせない（UI-20 / 設計 6.6）。説明は help.ts。 */
const LEGEND: readonly ChipJudgement[] = ['billable', 'no_data', 'excluded', 'failed', 'missing']

/** テナント行の色分けは判定とは別軸なので、凡例にも独立で並べる（設計 3.4 / 6.6）。 */
const TENANT_LEGEND_LABEL = 'デモテナント'

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

const missingRowStyle = {
  '--row-text': MISSING_TEXT_COLOR,
  '--row-fill': JUDGEMENT_FILLS.missing,
}
const tenantRowStyle = { '--tenant-fill': JUDGEMENT_FILLS.excluded }
const tenantLegendStyle = { '--swatch-fill': JUDGEMENT_FILLS.excluded }

const excludeKeywords = computed(
  () => result.value?.settings_snapshot?.exclude_keywords ?? DEFAULT_EXCLUDE_KEYWORDS,
)

/** 古い result.json は `tenant_excluded` を持たないので、設定スナップショットから補う（設計 7.3）。 */
function demoTenant(tenant: TenantResult): boolean {
  return tenant.tenant_excluded ?? isTenantExcluded(tenant, excludeKeywords.value)
}

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
    <HelpTip :text="HELP.runDetail.back">
      <template #default="{ props }">
        <v-btn
          v-bind="props"
          data-test="back-to-runs"
          variant="text"
          size="small"
          prepend-icon="mdi-arrow-left"
          :to="{ name: 'saas-a-billing.runs' }"
          class="mb-2"
        >
          実行履歴へ戻る
        </v-btn>
      </template>
    </HelpTip>
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
          <TriggerChip
            v-if="run"
            :trigger="run.trigger"
            data-test="trigger"
          />
          <span
            v-if="run?.saasa_host"
            class="text-body-2 text-medium-emphasis"
          >{{ run.saasa_host }}</span>
        </div>
      </div>
      <v-spacer />
      <!-- disabled なボタンはマウスイベントを受け取らないので、activator をラッパ側に置く -->
      <HelpTip :text="HELP.download">
        <template #default="{ props }">
          <span
            v-bind="props"
            class="d-inline-block"
          >
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
          </span>
        </template>
      </HelpTip>
      <span
        v-if="!canDownload"
        data-test="download-reason"
        class="text-caption text-medium-emphasis"
      >成功・配布済みの月だけダウンロードできます</span>
      <HelpTip :text="HELP.runDetail.rerun">
        <template #default="{ props }">
          <span
            v-bind="props"
            class="d-inline-block"
          >
            <v-btn
              data-test="rerun"
              color="primary"
              prepend-icon="mdi-refresh"
              :disabled="rerunBlocked"
              @click="openRerun"
            >
              再実行
            </v-btn>
          </span>
        </template>
      </HelpTip>
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

    <!-- 取得失敗があると配布を見送るため、配られなかった理由をここで伝える（#1006） -->
    <v-alert
      v-if="run?.distribute_note"
      data-test="distribute-note"
      type="warning"
      variant="tonal"
      density="compact"
      class="mb-4"
    >
      {{ run.distribute_note }}
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
              <HelpTip :text="HELP.summary[item.key]">
                <template #default="{ props }">
                  <div
                    v-bind="props"
                    class="text-caption text-medium-emphasis d-inline-block"
                  >
                    {{ item.label }}
                  </div>
                </template>
              </HelpTip>
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
            <HelpTip :text="HELP.runDetail.closingPeriod">
              <template #default="{ props }">
                <span
                  v-bind="props"
                  class="text-medium-emphasis me-2"
                >課金対象期間</span>
              </template>
            </HelpTip>
            {{ periodLabel(result.closing_period) }}
            <span class="text-medium-emphasis ms-2">
              （締め日時: 毎月 {{ result.closing_period.cutoff_day }} 日
              {{ result.closing_period.cutoff_time }}・観測データの日時基準・JST）
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
            <HelpTip
              v-for="filter in JUDGEMENT_FILTERS"
              :key="filter.value"
              :text="HELP.runDetail.judgementFilter"
            >
              <template #default="{ props }">
                <v-btn
                  v-bind="props"
                  :value="filter.value"
                >
                  {{ filter.label }}
                </v-btn>
              </template>
            </HelpTip>
          </v-btn-toggle>
          <HelpTip :text="HELP.runDetail.reviewOnly">
            <template #default="{ props }">
              <v-checkbox
                v-bind="props"
                v-model="reviewOnly"
                data-test="review-only"
                label="要確認のみ"
                density="compact"
                hide-details
              />
            </template>
          </HelpTip>
          <HelpTip :text="HELP.runDetail.boundaryOnly">
            <template #default="{ props }">
              <v-checkbox
                v-bind="props"
                v-model="boundaryOnly"
                data-test="boundary-only"
                label="境界日のみ受信"
                density="compact"
                hide-details
              />
            </template>
          </HelpTip>
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
          <HelpTip
            v-for="item in LEGEND"
            :key="item"
            :text="HELP.judgement[item]"
          >
            <template #default="{ props }">
              <span
                v-bind="props"
                class="d-inline-flex align-center ga-2 text-caption"
              >
                <span
                  class="legend-swatch"
                  :style="legendStyle(item)"
                />
                {{ JUDGEMENT_LABELS[item] }}
              </span>
            </template>
          </HelpTip>
          <HelpTip :text="HELP.tenantExcluded">
            <template #default="{ props }">
              <span
                v-bind="props"
                class="d-inline-flex align-center ga-2 text-caption"
              >
                <span
                  class="legend-swatch"
                  :style="tenantLegendStyle"
                />
                {{ TENANT_LEGEND_LABEL }}
              </span>
            </template>
          </HelpTip>
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
                  :key="item"
                >
                  {{ JUDGEMENT_LABELS[item] }}: {{ HELP.judgement[item] }}
                </li>
                <li>{{ TENANT_LEGEND_LABEL }}: {{ HELP.tenantExcluded }}</li>
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
              <tr
                data-test="tenant-row"
                class="tenant-row"
                :class="{ 'tenant-row--excluded': demoTenant(node.tenant) }"
                :style="tenantRowStyle"
              >
                <td colspan="10">
                  <span class="font-weight-bold">
                    ■ {{ node.tenant.tenant_cd }} {{ node.tenant.tenant_name }}
                  </span>
                  <HelpTip :text="HELP.runDetail.retention">
                    <template #default="{ props }">
                      <span
                        v-bind="props"
                        class="retention-badge ms-2"
                        :style="retentionStyle(node.tenant.retention_period)"
                      >{{ retentionLabel(node.tenant.retention_period) }}</span>
                    </template>
                  </HelpTip>
                  <span class="ms-2 text-body-2">
                    課金 {{ formatCount(node.tenant.counts.billable) }} 地点 ｜ 小計
                    <span class="font-weight-bold">{{ formatYen(node.tenant.subtotal) }}</span>
                  </span>
                  <span
                    v-if="node.tenant.status === 'failed'"
                    class="ms-2 text-body-2 text-error"
                  >取得失敗: {{ node.tenant.error }}</span>
                  <div
                    v-if="node.tenant.tenant_description"
                    data-test="tenant-description"
                    class="text-caption text-medium-emphasis"
                  >
                    {{ node.tenant.tenant_description }}
                  </div>
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
            前月分の結果にあり今回の地点一覧に無い地点です。金額には計上していません。
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
            <HelpTip :text="HELP.runDetail.priceTable">
              <template #default="{ props }">
                <span v-bind="props">使用単価表</span>
              </template>
            </HelpTip>
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
          <HelpTip :text="HELP.runDialog.distribute">
            <template #default="{ props }">
              <v-checkbox
                v-bind="props"
                v-model="rerunDistribute"
                data-test="rerun-distribute"
                label="メール・Slack へ配布する"
                density="compact"
                hide-details
              />
            </template>
          </HelpTip>
          <div
            data-test="distribute-targets"
            class="text-caption text-medium-emphasis ms-10 mb-2"
          >
            {{ distributeTargets }}
          </div>
          <HelpTip :text="HELP.runDialog.force">
            <template #default="{ props }">
              <v-checkbox
                v-bind="props"
                v-model="rerunForce"
                data-test="rerun-force"
                label="強制再実行（二重起動の防止を外す）"
                density="compact"
                hide-details
              />
            </template>
          </HelpTip>
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
          <HelpTip
            v-if="rerunDistribute"
            :text="HELP.runDialog.ack"
          >
            <template #default="{ props }">
              <v-checkbox
                v-bind="props"
                v-model="distributeAck"
                data-test="distribute-ack"
                label="上の配布先へ送ることを確認しました"
                density="compact"
                hide-details
                class="mt-2"
              />
            </template>
          </HelpTip>
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
          <HelpTip :text="HELP.runDialog.cancel">
            <template #default="{ props }">
              <v-btn
                v-bind="props"
                data-test="rerun-cancel"
                @click="rerunDialog = false"
              >
                キャンセル
              </v-btn>
            </template>
          </HelpTip>
          <HelpTip :text="HELP.runDialog.submit">
            <template #default="{ props }">
              <v-btn
                v-bind="props"
                data-test="rerun-confirm"
                :color="rerunDangerous ? 'error' : 'primary'"
                variant="flat"
                :disabled="!rerunConfirmed"
                @click="rerun"
              >
                {{ rerunDangerous ? '本番へ配布して実行' : '実行' }}
              </v-btn>
            </template>
          </HelpTip>
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

/* テナント見出しは既定でグレー。デモテナントだけ Excel の除外色で塗る（設計 6.6） */
.tenant-row > td {
  background-color: rgba(0, 0, 0, 0.04);
}

.tenant-row--excluded > td {
  background-color: var(--tenant-fill);
}

.missing-row {
  color: var(--row-text);
  background-color: var(--row-fill);
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
