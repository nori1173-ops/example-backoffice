<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { billingApi } from '../api'
import EnvBadge from '../components/EnvBadge.vue'
import JudgementChip, {
  JUDGEMENT_FILLS,
  MISSING_TEXT_COLOR,
} from '../components/JudgementChip.vue'
import StatusChip from '../components/StatusChip.vue'
import { useRunsStore } from '../stores/runs'
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

const DASH = '—'
const NO_GROUP = '(現場なし)'
/** billing.py の BOUNDARY_NOTE。備考の印であって判定・金額には影響しない（設計 2.5 / 6.5）。 */
const BOUNDARY_NOTE = '境界日のみ受信'
/** API は succeeded / distributed 以外の月を 404 にする（設計 8 章）。 */
const DOWNLOADABLE: readonly RunStatus[] = ['succeeded', 'distributed']

const SUMMARY_ITEMS: readonly { key: keyof BillingSummary; label: string; yen?: boolean }[] = [
  { key: 'tenant_count', label: 'テナント数' },
  { key: 'device_count', label: '地点数' },
  { key: 'billable_count', label: '課金' },
  { key: 'no_data_count', label: '通信なし' },
  { key: 'excluded_count', label: '除外' },
  { key: 'review_count', label: '要確認' },
  { key: 'failed_tenant_count', label: '取得失敗' },
  { key: 'missing_count', label: '消えた地点' },
  { key: 'total_amount', label: '合計金額', yen: true },
]

const JUDGEMENT_FILTERS: readonly { value: Judgement; label: string }[] = [
  { value: 'billable', label: '課金' },
  { value: 'no_data', label: '通信なし' },
  { value: 'excluded', label: '除外' },
  { value: 'failed', label: '取得失敗' },
]

const CAMERA_SOURCE_LABELS: Record<string, string> = { export: 'export', settings: '設定' }

/** 設計 6.6 の保存期間の色。モノクロ印刷でも文字で判別できるよう表示も変える。 */
const RETENTION_COLORS: Record<string, { fill: string; text: string }> = {
  '3': { fill: '#C6EFCE', text: '#006100' },
  '1': { fill: '#E4DFEC', text: '#3F3151' },
  unknown: { fill: 'transparent', text: '#C00000' },
}

const route = useRoute()
const store = useRunsStore()

const detail = ref<RunDetail | null>(null)
const settings = ref<SettingsView | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const judgementFilter = ref<Judgement[]>([])
const reviewOnly = ref(false)
const boundaryOnly = ref(false)
const rerunDialog = ref(false)
const rerunDistribute = ref(false)

const month = computed(() => String(route.params.month ?? ''))
const targetEnv = computed(() => String(route.params.targetEnv ?? '') as TargetEnv)
const run = computed(() => detail.value?.run ?? null)
const result = computed(() => detail.value?.result ?? null)

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function formatYen(value: number): string {
  return `¥${value.toLocaleString('ja-JP')}`
}

function formatCount(value: number): string {
  return value.toLocaleString('ja-JP')
}

/** JST オフセット付き ISO をそのままの壁時計として組み直す（環境の時差に依存させない）。 */
function toWallClock(iso: string): Date | null {
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/.exec(iso)
  if (!parts) {
    return null
  }
  const [year, mon, day, hour, minute, second] = parts.slice(1).map(Number)
  return new Date(year, mon - 1, day, hour, minute, second)
}

function formatSeconds(date: Date): string {
  return (
    `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}

/** 半開区間 `[from, to)` の終端から 1 秒引いた表示にする（設計 7.3）。 */
function periodLabel(period: ClosingPeriod): string {
  const from = toWallClock(period.from)
  const to = toWallClock(period.to)
  if (!from || !to) {
    return DASH
  }
  to.setSeconds(to.getSeconds() - 1)
  return `${formatSeconds(from)} 〜 ${formatSeconds(to)}`
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

const canDownload = computed(() => !!run.value && DOWNLOADABLE.includes(run.value.status))

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

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    detail.value = await billingApi.getRun(month.value, {
      targetEnv: targetEnv.value,
      detail: true,
    })
  } catch {
    error.value = '月次内訳の取得に失敗しました'
    detail.value = null
  } finally {
    loading.value = false
  }
}

async function loadSettings(): Promise<void> {
  try {
    settings.value = await billingApi.getSettings()
  } catch {
    settings.value = null
  }
}

async function rerun(): Promise<void> {
  rerunDialog.value = false
  await store.createRun({
    month: month.value,
    targetEnv: targetEnv.value,
    distribute: rerunDistribute.value,
    force: true,
  })
}

onMounted(() => Promise.all([load(), loadSettings()]))
</script>

<template>
  <div>
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
        variant="text"
        prepend-icon="mdi-file-excel-outline"
        @click="store.download(month, targetEnv)"
      >
        Excel
      </v-btn>
      <v-btn
        data-test="rerun"
        color="primary"
        prepend-icon="mdi-refresh"
        @click="rerunDialog = true"
      >
        再実行
      </v-btn>
    </div>

    <v-progress-linear
      :active="loading"
      indeterminate
      color="primary"
    />

    <v-alert
      v-if="error || store.error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ error || store.error }}
    </v-alert>

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
                {{ item.yen ? formatYen(result.summary[item.key]) : formatCount(result.summary[item.key]) }}
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
        <v-table
          density="comfortable"
          class="text-no-wrap"
        >
          <thead>
            <tr>
              <th>地点CD</th>
              <th>地点名</th>
              <th>フォーマット</th>
              <th class="text-end">
                項目数
              </th>
              <th>カメラ</th>
              <th>最終受信日時</th>
              <th>判定</th>
              <th class="text-end">
                単価
              </th>
              <th class="text-end">
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
                    {{ formatYen(node.tenant.subtotal) }}
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
                  <td class="ps-8">
                    {{ device.device_cd }}
                  </td>
                  <td>{{ device.device_name }}</td>
                  <td>{{ device.format_name }}</td>
                  <td class="text-end">
                    {{ formatCount(device.item_count) }}
                  </td>
                  <td>{{ cameraLabel(device) }}</td>
                  <td>{{ device.last_received_at ?? DASH }}</td>
                  <td>
                    <JudgementChip
                      :judgement="device.judgement"
                      :flags="device.flags"
                    />
                  </td>
                  <td class="text-end">
                    {{ formatYen(device.unit_price) }}
                  </td>
                  <td class="text-end">
                    {{ formatYen(device.amount) }}
                  </td>
                  <td>{{ [device.device_description, device.note].filter(Boolean).join(' / ') }}</td>
                </tr>
              </template>
            </template>
          </tbody>
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
        <v-table
          density="comfortable"
          class="text-no-wrap"
        >
          <thead>
            <tr>
              <th>テナントCD</th>
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
              <td>{{ device.tenant_cd }}</td>
              <td>{{ device.tenant_name }}</td>
              <td>{{ device.group_names.join('、') }}</td>
              <td>{{ device.device_cd }}</td>
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
            {{ result.price_table.effective_from.replace(/-/g, '/') }} 適用版
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
          <p class="mb-3">
            {{ targetEnv === 'production' ? '本番' : '検証環境' }}の {{ month }} を再集計します。
            成功済みの結果は上書きされます。
          </p>
          <v-checkbox
            v-model="rerunDistribute"
            data-test="rerun-distribute"
            label="メール・Slack へ配布する"
            density="compact"
            hide-details
          />
          <div
            data-test="distribute-targets"
            class="text-caption text-medium-emphasis ms-10"
          >
            {{ distributeTargets }}
          </div>
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
            color="primary"
            variant="flat"
            @click="rerun"
          >
            実行
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.device-row {
  background-color: var(--row-fill);
}

.missing-row {
  color: var(--row-text);
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
