<script setup lang="ts">
import { computed, onActivated, onDeactivated, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import HelpTip from '@/components/HelpTip.vue'
import { useAuthStore } from '@/stores/auth'
import { formatCount, formatDateTime } from '@/utils/format'
import CounterTable from '../components/CounterTable.vue'
import EvidenceTable from '../components/EvidenceTable.vue'
import LevelChip from '../components/LevelChip.vue'
import LogDialog from '../components/LogDialog.vue'
import { useLogQuery } from '../composables/useLogQuery'
import { HELP } from '../help'
import { useDaysStore } from '../stores/days'
import { formatBytes } from '../utils/format'
import { isLogRef } from '../utils/logs'
import { SECTION_FIELDS } from '../utils/settings'
import { TABLE_LABELS, TOP_SECTIONS, counterSection, evidenceNames, formatTimeRange, isConsoleLink } from '../utils/trace'
import type { EvidenceRow, LogRef, Section } from '../types'

const route = useRoute()
const store = useDaysStore()
const auth = useAuthStore()
const isAdmin = computed(() => auth.groups.includes('admin'))
const logs = useLogQuery()
const logOpen = ref(false)
const logTarget = ref<{ ref: LogRef; link: string | null } | null>(null)
const date = computed(() => String(route.params.date ?? ''))
const day = computed(() => store.details[date.value])
const summary = computed(() => day.value?.summary)
const ai = computed(() => day.value?.ai ?? summary.value?.ai)
const s3Source = computed(() => summary.value?.sources.s3)
const s3Buckets = computed(() => summary.value?.top?.s3_buckets ?? [])
const actors = computed(() => summary.value?.actors)
const actorGroups = [{ key: 'dormant', title: '休眠主体の利用' }, { key: 'new', title: '初見の主体' }, { key: 'external', title: '他アカウントからのアクセス' }] as const
const inventoryGroups = [{ key: 'users', title: '未使用のユーザー' }, { key: 'keys', title: '未使用の有効キー' }, { key: 'mfa_missing', title: 'MFA なしのコンソールユーザー' }] as const
const showActors = computed(() => actors.value && (['unavailable', 'initializing'].includes(actors.value.status) || actors.value.inventory
  || actorGroups.some(group => actors.value?.[group.key].length)))
const glossary = computed(() => day.value?.glossary ?? null)
const sectionLabel = (key: string) => SECTION_FIELDS.find(section => section.key === key)?.label ?? key
const sectionHelp = (key: Section) => HELP[`section_${key}`]
const tableLabels = TABLE_LABELS
function evidence(section: Section): Record<string, (EvidenceRow | string)[]> {
  const source = section === 's3' ? undefined : summary.value?.sources[section]
  const tables: Record<string, (EvidenceRow | string)[]> = { ...source?.details }
  if (source?.config_changes) tables.config_changes = source.config_changes
  if (section === 'instances') Object.assign(tables, summary.value?.instances)
  return tables
}
function topTables(section: Section): [string, EvidenceRow[]][] {
  return Object.entries(summary.value?.top ?? {}).filter(([key, rows]) => TOP_SECTIONS[key] === section && Array.isArray(rows))
}
function counters(section: Section): Record<string, number> {
  return Object.fromEntries(Object.entries(summary.value?.counters ?? {}).filter(([key]) => counterSection(key) === section))
}
function signedCount(value: number): string {
  return `${value > 0 ? '+' : ''}${formatCount(value)}`
}
function percentChange(value: number | null): string {
  return value === null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(1)}%`
}
const findings = (section: Section) => (summary.value?.findings ?? []).filter(finding => finding.section === section)
const aiSection = (section: Section) => ai.value?.sections?.find(item => item.section === section)
const aiKeys = (section: Section) => aiSection(section)?.keys ?? []
const basis = (section: Section) => evidenceNames(aiKeys(section), glossary.value, summary.value?.findings ?? [])
const sourceStatus = { ok: '取得済み', failed: '未集計', disabled: '停止中（設定）' }
const aiReason = computed(() => {
  if (day.value?.legacy) return '取得できず（v1 は AI 判定なし）'
  const error = ai.value?.error
  const reason = typeof error === 'string' ? error : error?.message
  if (ai.value?.status === 'disabled') return '取得できず（設定で無効）'
  if (!reason && ai.value?.opinion) return ai.value.opinion
  const statuses = { failed: '失敗', refused: '拒否', timeout: '時間切れ', skipped: '呼ぶ時間不足', ok: '情報なし' }
  return `取得できず（${reason || (ai.value ? statuses[ai.value.status] : '情報なし')}）`
})
function openLog(target: LogRef, link?: string | null): void {
  if (!isAdmin.value) return
  logTarget.value = { ref: target, link: isConsoleLink(link) ? link : null }
  logOpen.value = true
  void logs.open(date.value, target)
}
watch(() => route.name === 'daily-security-report.day' ? date.value : null, value => { if (value) void store.fetchDay(value) })
watch(logOpen, open => { if (!open) logs.close() })
watch([isAdmin, date], () => { logOpen.value = false })
onActivated(() => store.fetchDay(date.value))
onDeactivated(() => { logOpen.value = false })
</script>

<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <h1 class="text-h5">
        レポートの詳細
      </h1><v-spacer />
      <v-btn
        data-test="back-to-days"
        prepend-icon="mdi-arrow-left"
        :to="{ name: 'daily-security-report.days' }"
      >
        一覧へ戻る
      </v-btn>
    </div>
    <v-progress-linear
      v-if="store.detailLoading[date]"
      indeterminate
      class="mb-4"
    />
    <v-alert
      v-if="store.detailErrors[date]"
      data-test="day-detail-error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ store.detailErrors[date] }}
      <v-btn
        v-if="!store.detailNotFound[date]"
        variant="text"
        @click="store.fetchDay(date, true)"
      >
        再取得
      </v-btn>
    </v-alert>
    <template v-else-if="day">
      <v-card
        variant="flat"
        border
        class="mb-4"
        data-test="day-summary"
      >
        <v-card-text>
          <div class="d-flex align-center flex-wrap ga-3 mb-4">
            <h2 class="text-h6">
              {{ day.date }}
            </h2>
            <HelpTip :text="HELP.level">
              <template #default="{ props }">
                <LevelChip
                  v-bind="props"
                  :level="day.status === 'succeeded' ? day.level : null"
                  :legacy="day.legacy"
                />
              </template>
            </HelpTip>
          </div>
          <p
            v-if="day.status !== 'succeeded'"
            class="mb-3"
          >
            {{ day.status === 'running' ? '実行中' : '失敗' }} {{ day.error }}
          </p>
          <HelpTip :text="HELP.aiOpinion">
            <template #default="{ props }">
              <h3
                v-bind="props"
                class="text-subtitle-1"
              >
                AI の見解
              </h3>
            </template>
          </HelpTip>
          <p
            data-test="detail-ai-opinion"
            class="opinion"
          >
            {{ day.ai_opinion || (ai?.status === 'ok' ? ai.opinion : aiReason) }}
          </p>
          <p class="text-body-2 mt-2">
            {{ ai?.model_label || 'モデル情報なし' }}
          </p>
          <p class="text-caption text-medium-emphasis model-info">
            モデル ID: {{ ai?.model_id || '—' }} / プロンプトのハッシュ: {{ ai?.prompt_hash || '—' }}
          </p>
          <p
            v-if="ai?.note"
            class="text-body-2 mt-2"
          >
            {{ ai.note }}
          </p>
          <template v-if="ai?.adjustments?.length">
            <h3 class="text-subtitle-2 mt-4">
              AI が変えた段階
            </h3>
            <div class="table-scroll">
              <v-table
                density="compact"
                aria-label="AI が変えた段階"
              >
                <thead><tr><th>区分</th><th>規則</th><th>AI</th><th>理由</th></tr></thead>
                <tbody>
                  <tr
                    v-for="adjustment in ai.adjustments"
                    :key="adjustment.section"
                  >
                    <td>{{ sectionLabel(adjustment.section) }}</td><td><LevelChip :level="adjustment.from" /></td><td><LevelChip :level="adjustment.to" /></td><td>{{ adjustment.reason }}</td>
                  </tr>
                </tbody>
              </v-table>
            </div>
          </template>
        </v-card-text>
      </v-card>
      <template v-if="summary">
        <v-card
          v-for="section in SECTION_FIELDS"
          :key="section.key"
          variant="flat"
          border
          class="mb-4"
          :data-test="`detail-${section.key}`"
        >
          <v-card-text>
            <div class="d-flex align-center flex-wrap ga-2 mb-3">
              <HelpTip :text="sectionHelp(section.key)">
                <template #default="{ props }">
                  <h2
                    v-bind="props"
                    class="text-h6"
                  >
                    {{ section.label }}
                  </h2>
                </template>
              </HelpTip>
              <span class="text-caption">最終</span><LevelChip :level="summary.levels[section.key]" />
              <span class="text-caption">規則</span><LevelChip :level="summary.rule_levels[section.key]" />
            </div>
            <p class="text-body-2 mb-2">
              所見 {{ formatCount(findings(section.key).length + (summary.findings_truncated?.[section.key] ?? 0)) }} 件
            </p>
            <p
              v-if="ai?.status === 'ok'"
              class="text-body-2 mb-1"
            >
              {{ aiSection(section.key)?.reason }}
            </p>
            <p
              v-if="ai?.status === 'ok' && aiKeys(section.key).length"
              data-test="ai-basis"
              class="text-caption text-medium-emphasis mb-2"
            >
              根拠: {{ basis(section.key).join('、') }}
            </p>
            <div
              v-if="findings(section.key).length"
              class="table-scroll"
            >
              <v-table
                density="compact"
                :aria-label="`${section.label} の所見`"
              >
                <thead><tr><th>段階</th><th>内容</th><th>件数</th><th>時刻</th><th>場所</th></tr></thead>
                <tbody>
                  <tr
                    v-for="(finding, index) in findings(section.key)"
                    :key="index"
                    data-test="finding-row"
                  >
                    <td><LevelChip :level="finding.level" /></td>
                    <td>
                      {{ finding.text }}
                      <div class="text-caption text-medium-emphasis d-flex align-center flex-wrap ga-1">
                        {{ finding.code }}
                        <v-chip
                          v-if="aiKeys(section.key).includes(finding.code)"
                          data-test="ai-key-chip"
                          size="x-small"
                          variant="tonal"
                          color="primary"
                          label
                        >
                          AI の根拠
                        </v-chip>
                      </div>
                    </td>
                    <td>{{ formatCount(finding.count) }}</td>
                    <td class="text-no-wrap">
                      {{ formatTimeRange(finding.first_at, finding.last_at) }}
                    </td>
                    <td class="finding-where">
                      <template v-if="finding.where || finding.link">
                        {{ finding.where }}
                        <a
                          v-if="isConsoleLink(finding.link)"
                          :href="finding.link"
                          target="_blank"
                          rel="noopener"
                        >コンソール</a>
                        <template v-else-if="finding.link">
                          {{ finding.link }}
                        </template>
                      </template>
                      <template v-else>
                        —
                      </template>
                      <div
                        v-if="isAdmin && isLogRef(finding.ref)"
                        class="mt-1"
                      >
                        <v-btn
                          data-test="log-button"
                          size="x-small"
                          variant="tonal"
                          @click="openLog(finding.ref, finding.link)"
                        >
                          ログを見る
                        </v-btn>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </v-table>
            </div>
            <p
              v-if="summary.findings_truncated?.[section.key]"
              class="text-caption"
            >
              ほか {{ formatCount(summary.findings_truncated[section.key]!) }} 件の所見
            </p>
            <template
              v-for="(rows, key) in evidence(section.key)"
              :key="key"
            >
              <h3 class="text-subtitle-2 mt-4 mb-2">
                {{ tableLabels[key] ?? key }}
              </h3>
              <EvidenceTable
                :rows="rows"
                :label="tableLabels[key] ?? key"
                :logs="isAdmin"
                @log="openLog"
              />
            </template>
            <template
              v-for="[key, rows] in topTables(section.key)"
              :key="`top-${key}`"
            >
              <h3 class="text-subtitle-2 mt-4 mb-2">
                {{ tableLabels[key] ?? key }}
              </h3>
              <EvidenceTable
                :rows="rows"
                :label="tableLabels[key] ?? key"
                :logs="isAdmin"
                @log="openLog"
              />
              <p
                v-if="summary.top_truncated?.[key]"
                class="text-caption"
              >
                ほか {{ formatCount(summary.top_truncated[key]) }} 件
              </p>
            </template>
            <template v-if="Object.keys(counters(section.key)).length">
              <h3 class="text-subtitle-2 mt-4 mb-2">
                集計値
              </h3>
              <CounterTable
                :label="`${section.label} の集計値`"
                :counters="counters(section.key)"
                :comparisons="summary.comparisons"
                :hourly="summary.sources.metrics?.hourly"
                :glossary="glossary"
                :ai-keys="aiKeys(section.key)"
                :logs="isAdmin"
                @log="openLog"
              />
            </template>
            <template v-if="section.key === 's3'">
              <template v-if="s3Buckets.length">
                <h3 class="text-subtitle-2 mt-4 mb-2">
                  バケット
                </h3>
                <div class="table-scroll">
                  <v-table
                    density="compact"
                    aria-label="S3 バケット"
                    data-test="s3-buckets"
                    class="s3-buckets"
                  >
                    <thead><tr><th>バケット</th><th>リージョン</th><th>オブジェクト数</th><th>前日差</th><th>容量</th><th>前日比</th><th>ダウンロード量</th><th>削除要求</th><th>判定</th></tr></thead>
                    <tbody>
                      <tr
                        v-for="bucket in s3Buckets"
                        :key="bucket.key"
                      >
                        <td class="s3-bucket-name">
                          <a
                            v-if="isConsoleLink(bucket.link)"
                            :href="bucket.link"
                            target="_blank"
                            rel="noopener"
                          >{{ bucket.bucket }}</a>
                          <span v-else>{{ bucket.bucket }}</span>
                        </td>
                        <td>{{ bucket.region }}</td>
                        <td>{{ formatCount(bucket.objects) }}</td>
                        <td :class="{ 'text-error': bucket.objects_diff !== null && bucket.objects_diff < 0 }">
                          <template v-if="bucket.objects_diff !== null">
                            {{ signedCount(bucket.objects_diff) }} 件 ({{ percentChange(bucket.objects_pct) }})
                          </template>
                          <template v-else>
                            —
                          </template>
                        </td>
                        <td>{{ formatBytes(bucket.size) }}</td>
                        <td :class="{ 'text-error': bucket.size_pct !== null && bucket.size_pct < 0 }">
                          {{ percentChange(bucket.size_pct) }}
                        </td>
                        <td>{{ formatBytes(bucket.metrics ? bucket.bytes_downloaded : null) }}</td>
                        <td>{{ formatCount(bucket.metrics ? bucket.delete_requests : null) }}</td>
                        <td><LevelChip :level="bucket.level" /></td>
                      </tr>
                    </tbody>
                  </v-table>
                </div>
              </template>
              <p class="text-body-2 mt-4">
                取得状況: {{ s3Source ? sourceStatus[s3Source.status] : '情報なし' }}<span v-if="s3Source?.error">（{{ s3Source.error }}）</span>
              </p>
              <p
                v-if="s3Source?.note"
                class="text-body-2"
              >
                {{ s3Source.note }}
              </p>
              <p
                v-if="s3Source?.regions?.length"
                class="text-caption"
              >
                リージョン: {{ s3Source?.regions?.join(' / ') }}
              </p>
            </template>
            <template v-else>
              <p class="text-body-2 mt-4">
                取得状況: {{ summary.sources[section.key] ? sourceStatus[summary.sources[section.key]!.status] : '情報なし' }}<span v-if="summary.sources[section.key]?.error">（{{ summary.sources[section.key]?.error }}）</span>
              </p>
              <p
                v-if="summary.sources[section.key]?.regions"
                class="text-caption"
              >
                リージョン: {{ Object.entries(summary.sources[section.key]!.regions!).map(([region, status]) => `${region}: ${status}`).join(' / ') }}
              </p>
              <p
                v-if="summary.sources[section.key]?.missing_targets?.length"
                class="text-caption"
              >
                指標なし: {{ summary.sources[section.key]?.missing_targets?.join('、') }}
              </p>
              <p
                v-if="summary.sources[section.key]?.truncated?.length"
                class="text-caption"
              >
                取得上限: {{ summary.sources[section.key]?.truncated?.join('、') }}
              </p>
              <p
                v-for="(count, key) in summary.sources[section.key]?.details_truncated"
                :key="key"
                class="text-caption"
              >
                {{ tableLabels[key] ?? key }}: ほか {{ formatCount(count) }} 件
              </p>
              <p
                v-for="(note, index) in summary.sources[section.key]?.notes"
                :key="index"
                class="text-caption"
              >
                {{ note }}
              </p>
            </template>
          </v-card-text>
        </v-card>
        <v-card
          v-if="showActors && actors"
          variant="flat"
          border
          class="mb-4"
          data-test="detail-actors"
        >
          <v-card-text>
            <HelpTip :text="HELP.actors">
              <template #default="{ props }">
                <h2
                  v-bind="props"
                  class="text-h6 mb-3"
                >
                  休眠主体と棚卸し
                </h2>
              </template>
            </HelpTip>
            <p v-if="actors.status !== 'ok'">
              休眠判定: {{ actors.status === 'unavailable' ? '取得できず' : actors.status === 'initializing' ? '初期化中（前回不明）' : '停止中（設定）' }}
            </p>
            <p
              v-if="actors.note"
              class="text-body-2"
            >
              {{ actors.note }}
            </p>
            <template
              v-for="group in actorGroups"
              :key="group.key"
            >
              <template v-if="actors[group.key].length || actors.truncated?.[group.key]">
                <h3 class="text-subtitle-2 mt-4 mb-2">
                  {{ group.title }}
                </h3><EvidenceTable
                  :rows="actors[group.key]"
                  :label="group.title"
                />
                <p
                  v-if="actors.truncated?.[group.key]"
                  class="text-caption"
                >
                  ほか {{ formatCount(actors.truncated[group.key]!) }} 件
                </p>
              </template>
            </template>
            <template v-if="actors.inventory">
              <h3 class="text-subtitle-1 mt-4">
                認証情報の棚卸し
              </h3>
              <p class="text-body-2">
                取得状況: {{ actors.inventory.status === 'ok' ? '取得済み' : '取得できず' }} {{ actors.inventory.note }}
              </p>
              <p
                v-if="actors.inventory.generated_at"
                class="text-caption"
              >
                レポート生成: {{ formatDateTime(actors.inventory.generated_at) }}
              </p>
              <template
                v-for="group in inventoryGroups"
                :key="group.key"
              >
                <h4 class="text-subtitle-2 mt-4 mb-2">
                  {{ group.title }}（{{ formatCount(actors.inventory.counts[group.key] ?? actors.inventory[group.key].length) }} 件）
                </h4>
                <EvidenceTable
                  :rows="actors.inventory[group.key]"
                  :label="group.title"
                />
                <p
                  v-if="actors.inventory.truncated?.[group.key]"
                  class="text-caption"
                >
                  ほか {{ formatCount(actors.inventory.truncated[group.key]!) }} 件
                </p>
              </template>
            </template>
          </v-card-text>
        </v-card>
        <div
          class="text-body-2 text-medium-emphasis"
          data-test="detail-footer"
        >
          <p>集計範囲: {{ formatDateTime(summary.range.from) }} 〜 {{ formatDateTime(summary.range.to) }}（終端を含まない、JST）</p>
          <p>除外 {{ formatCount(Object.values(summary.excluded).reduce((total, count) => total + count, 0)) }} 件 / 所要 {{ formatCount(summary.duration_sec) }} 秒</p>
          <p>基準なし: {{ (summary.baseline_missing ?? []).length ? (summary.baseline_missing ?? []).map(sectionLabel).join('、') : 'なし' }}</p>
        </div>
      </template>
    </template>
    <LogDialog
      v-if="isAdmin"
      v-model="logOpen"
      :log-ref="logTarget?.ref ?? null"
      :status="logs.status.value"
      :result="logs.result.value"
      :error="logs.error.value"
      :error-code="logs.errorCode.value"
      :link="logTarget?.link ?? null"
      :glossary="glossary"
    />
  </div>
</template>

<style scoped>
.table-scroll { overflow-x: auto; }
.s3-buckets { min-width: 1100px; }
.s3-bucket-name { overflow-wrap: anywhere; min-width: 160px; }
.opinion { white-space: pre-wrap; overflow-wrap: anywhere; }
.model-info { overflow-wrap: anywhere; }
.finding-where { overflow-wrap: anywhere; min-width: 160px; }
</style>
