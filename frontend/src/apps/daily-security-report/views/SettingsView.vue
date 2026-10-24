<script setup lang="ts">
import { computed, onActivated, onDeactivated, onUnmounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import HelpTip from '@/components/HelpTip.vue'
import TriggerSuggestField, { type SuggestItem } from '@/components/TriggerSuggestField.vue'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { formatDateTime } from '@/utils/format'
import LevelChip from '../components/LevelChip.vue'
import PromptPresetCards from '../components/PromptPresetCards.vue'
import RunNowCard from '../components/RunNowCard.vue'
import { useAiPreview } from '../composables/useAiPreview'
import { useDaysStore } from '../stores/days'
import { HELP } from '../help'
import { useSettingsStore } from '../stores/settings'
import type { EditableSettings, SettingsView } from '../types'
import { joinMentions, mentionFallbackLabel, parseMentions } from '../utils/slackMention'
import { effectivePrompt } from '../utils/prompt'
import { emptySettings, exclusionExpired, parseMetricTargets, MENTION_LEVELS, MODEL_FAMILIES, SECTION_FIELDS, THRESHOLD_FIELDS, validateAiSettings, validateSettings } from '../utils/settings'

const auth = useAuthStore()
const ui = useUiStore()
const store = useSettingsStore()
const daysStore = useDaysStore()
const preview = useAiPreview()
const previewDate = ref('')
const isAdmin = computed(() => auth.groups.includes('admin'))
const editable = computed(() => isAdmin.value && !!store.settings && !store.loading)
const form = reactive<EditableSettings>(emptySettings())
const metricTargetsText = ref('{}')
const pendingMail = reactive({ mail_to: '', mail_cc: '' })
const snapshot = ref('')
const leaveDialog = ref(false)
let leaveResolve: ((leave: boolean) => void) | null = null
const serialize = () => JSON.stringify({ form, metricTargetsText: metricTargetsText.value })
snapshot.value = serialize()
const isDirty = computed(() => serialize() !== snapshot.value || !!pendingMail.mail_to || !!pendingMail.mail_cc)

const toggles = [
  { key: 'auto_run_enabled', label: 'レポートの自動実行を有効にする' },
  { key: 'alert_enabled', label: 'GuardDuty の即時通知を有効にする（東京のみ）' },
  { key: 'mail_enabled', label: 'メール配布を有効にする' },
  { key: 'slack_enabled', label: 'Slack 配布を有効にする' },
] as const
const mailFields = [{ key: 'mail_to', label: 'メール宛先 To' }, { key: 'mail_cc', label: 'メール宛先 Cc' }] as const
const kinds = ['ip', 'principal', 'event', 'instance']
const scopes = ['cloudtrail', 'insights', 'gps', 'instances', 'all']
const exclusionColumns = [
  { key: 'kind', label: '種類' }, { key: 'value', label: '値' }, { key: 'scope', label: '適用先' },
  { key: 'category', label: '区分' }, { key: 'reason', label: '理由' }, { key: 'expires_at', label: '期限' },
] as const

const previewDays = computed(() => daysStore.days.filter(day => day.status === 'succeeded' && day.date).map(day => day.date!))
const defaultPrompt = computed(() => store.settings?.ai_prompt_default ?? '')
const promptToSave = computed(() => effectivePrompt(form.ai_prompt, defaultPrompt.value))
const promptFixed = computed(() => store.settings?.ai_prompt_fixed)
const previewRequest = computed(() => ({ date: previewDate.value, prompt: promptToSave.value, model_family: form.ai_model_family, model_pin: form.ai_model_pin }))
const canPreview = computed(() => editable.value && previewDays.value.includes(previewDate.value)
  && !Object.keys(validateAiSettings(form)).length && !preview.busy.value)
const previewChanged = computed(() => preview.requested.value && JSON.stringify(preview.requested.value) !== JSON.stringify(previewRequest.value))
const weekdays = [{ title: '無効', value: null }, ...['月', '火', '水', '木', '金', '土', '日'].map((day, value) => ({ title: `${day}曜日`, value }))]
const sectionLabel = (key: string) => SECTION_FIELDS.find(section => section.key === key)?.label ?? key
watch(previewDays, dates => { if (!dates.includes(previewDate.value)) previewDate.value = dates[0] ?? '' })
watch(isAdmin, admin => { if (!admin) preview.stop() })
function seed(settings: SettingsView | null): void {
  if (!settings) return
  const defaults = emptySettings()
  for (const key of Object.keys(defaults) as (keyof EditableSettings)[]) {
    Object.assign(form, { [key]: JSON.parse(JSON.stringify((settings[key] === undefined ? defaults[key] : settings[key]))) })
  }
  form.ai_prompt = settings.ai_prompt || settings.ai_prompt_default || ''
  metricTargetsText.value = JSON.stringify(form.metric_targets, null, 2)
  pendingMail.mail_to = ''
  pendingMail.mail_cc = ''
  snapshot.value = serialize()
}
function commitPending(event: FocusEvent, field: 'mail_to' | 'mail_cc'): void {
  const value = (event.target as HTMLInputElement | null)?.value?.trim() ?? pendingMail[field].trim()
  if (value && !form[field].includes(value)) form[field].push(value)
  pendingMail[field] = ''
}
const errors = computed(() => {
  const found = validateSettings(form)
  try { parseMetricTargets(metricTargetsText.value) } catch (error) {
    found.metric_targets = error instanceof Error ? error.message : '指標対象の JSON が不正です'
  }
  return found
})
const canSave = computed(() => editable.value && !Object.keys(errors.value).length)
const channelItems = computed<SuggestItem[]>(() => (store.directory?.channels ?? []).map(channel => ({
  value: channel.id, label: `#${channel.name}`, hint: channel.is_private ? '非公開' : undefined, keywords: [channel.name],
})))
const mentionItems = computed<SuggestItem[]>(() => [
  { value: '<!here>', label: '@here', hint: 'オンライン全員' },
  { value: '<!channel>', label: '@channel', hint: 'チャンネル全員' },
  ...(store.directory?.users ?? []).map(user => ({ value: `<@${user.id}>`,
    label: `@${user.display_name || user.real_name}`, hint: `${user.real_name} · @${user.handle}`,
    keywords: [user.handle, user.real_name, user.display_name] })),
])
const slackChannelValues = computed({
  get: () => form.slack_channel_id ? [form.slack_channel_id] : [],
  set: (values: string[]) => {
    const id = values[0] ?? ''
    form.slack_channel_id = id
    form.slack_channel_label = id ? channelItems.value.find(item => item.value === id)?.label ?? form.slack_channel_label : ''
  },
})
const slackMentionValues = computed({
  get: () => parseMentions(form.slack_mention),
  set: (values: string[]) => { form.slack_mention = joinMentions(values) },
})
const channelFallbackLabel = (id: string) => form.slack_channel_label || `#${id}`

function addExclusion(): void {
  if (!editable.value || form.exclusions.length >= 200) return
  form.exclusions.push({ kind: 'ip', value: '', scope: ['all'], category: '', reason: '', expires_at: '' })
}
function deleteExclusion(index: number): void {
  if (editable.value) form.exclusions.splice(index, 1)
}
async function save(): Promise<void> {
  if (!canSave.value) return
  const patch = JSON.parse(JSON.stringify(form)) as EditableSettings
  patch.metric_targets = parseMetricTargets(metricTargetsText.value)
  patch.ai_prompt = promptToSave.value
  for (const field of THRESHOLD_FIELDS) patch[field.key] = Number(form[field.key])
  const ok = await store.save(patch)
  if (ok) {
    seed(store.settings)
    ui.notify('設定を保存しました', 'success')
  } else {
    ui.notify(store.error ?? '設定の保存に失敗しました', 'error')
  }
}
async function discardChanges(): Promise<void> {
  if (!editable.value) return
  await store.fetch()
  if (!store.error) seed(store.settings)
}
function answerLeave(leave: boolean): void {
  leaveDialog.value = false
  if (leave) seed(store.settings)
  leaveResolve?.(leave)
  leaveResolve = null
}
onBeforeRouteLeave(async () => {
  if (!isDirty.value) return true
  leaveDialog.value = true
  return await new Promise<boolean>(resolve => { leaveResolve = resolve })
})
function warnUnload(event: BeforeUnloadEvent): void {
  if (isDirty.value) {
    event.preventDefault()
    event.returnValue = ''
  }
}
watch(() => store.settings, seed, { immediate: true })
onActivated(async () => {
  window.addEventListener('beforeunload', warnUnload)
  if (isAdmin.value && !store.directory && !store.directoryLoading) void store.loadSlackDirectory()
  if (isAdmin.value) void daysStore.fetch()
  if (!isDirty.value) await store.fetch()
})
onDeactivated(() => { window.removeEventListener('beforeunload', warnUnload); preview.stop() })
onUnmounted(() => {
  window.removeEventListener('beforeunload', warnUnload)
  leaveResolve?.(false)
})
</script>

<template>
  <div>
    <h1 class="text-h5 mb-1">
      設定
    </h1>
    <p class="text-body-2 text-medium-emphasis mb-4">
      AWS日次セキュリティレポートの監視区分・通知先・判定条件を管理します。
    </p>
    <HelpTip :text="HELP.target">
      <template #default="{ props }">
        <v-card
          v-bind="props"
          variant="flat"
          border
          class="mb-4"
        >
          <v-card-text data-test="target-summary">
            <div class="text-subtitle-1">
              集計対象: 本番アカウント {{ store.settings?.target_account || '—' }}（固定）
            </div>
            <div class="text-body-2 text-medium-emphasis">
              このサイトでは固定です（画面からは変更できません）。
            </div>
            <div class="text-body-2 mt-2">
              Slack の動作方式: {{ store.settings?.slack_mode === 'bot' ? 'Bot token' : store.settings?.slack_mode === 'webhook' ? 'Webhook フォールバック' : '未取得' }}
            </div>
          </v-card-text>
        </v-card>
      </template>
    </HelpTip>
    <v-alert
      v-if="!isAdmin"
      data-test="readonly-notice"
      type="info"
      variant="tonal"
      class="mb-4"
    >
      読み取り専用です。設定の変更には admin 権限が必要です。
    </v-alert>
    <v-alert
      v-if="store.settings?.slack_mode === 'webhook'"
      data-test="webhook-notice"
      type="warning"
      variant="tonal"
      class="mb-4"
    >
      Webhook フォールバック中です。選んだチャンネルは無視され、投稿先は Webhook 側で固定されます。Block Kit は使われず、テキストだけで投稿されます。
    </v-alert>
    <v-alert
      v-if="store.error"
      data-test="settings-error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ store.error }}
    </v-alert>
    <v-progress-linear
      v-if="store.loading"
      indeterminate
      class="mb-4"
    />
    <RunNowCard
      :disabled="!editable"
      :dirty="isDirty"
    />
    <v-card
      variant="flat"
      border
      class="mb-4"
      data-test="ai-settings"
    >
      <v-card-text>
        <HelpTip :text="HELP.ai">
          <template #default="{ props }">
            <h2
              v-bind="props"
              class="text-h6 mb-3"
            >
              AI 判定
            </h2>
          </template>
        </HelpTip>
        <HelpTip :text="HELP.ai_enabled">
          <template #default="{ props }">
            <v-switch
              v-bind="props"
              v-model="form.ai_enabled"
              data-test="ai-enabled"
              label="AI 判定を有効にする"
              :disabled="!editable"
              color="primary"
              hide-details
              class="mb-4"
            />
          </template>
        </HelpTip>
        <HelpTip :text="HELP.ai_model_family">
          <template #default="{ props }">
            <v-select
              v-bind="props"
              v-model="form.ai_model_family"
              data-test="ai-model-family"
              label="AI の系統"
              :items="MODEL_FAMILIES"
              :disabled="!editable"
              :error-messages="errors.ai_model_family"
              variant="outlined"
              :hint="HELP.ai_model_family"
              persistent-hint
            />
          </template>
        </HelpTip>
        <v-expansion-panels
          variant="accordion"
          class="my-4"
        >
          <v-expansion-panel
            title="詳細設定（モデルのピン留め）"
            data-test="ai-advanced"
          >
            <v-expansion-panel-text>
              <HelpTip :text="HELP.ai_model_pin">
                <template #default="{ props }">
                  <v-text-field
                    v-bind="props"
                    v-model="form.ai_model_pin"
                    data-test="ai-model-pin"
                    label="モデル ID（空欄は自動）"
                    :disabled="!editable"
                    :error-messages="errors.ai_model_pin"
                    :hint="HELP.ai_model_pin"
                    persistent-hint
                    variant="outlined"
                  />
                </template>
              </HelpTip>
            </v-expansion-panel-text>
          </v-expansion-panel>
        </v-expansion-panels>
        <div class="d-flex align-center flex-wrap ga-2 mb-2">
          <v-chip
            data-test="ai-prompt-state"
            size="small"
            :color="promptToSave ? 'warning' : 'primary'"
            variant="tonal"
          >
            {{ promptToSave ? 'カスタム' : '既定' }}
          </v-chip>
          <v-spacer />
          <v-btn
            data-test="ai-prompt-reset"
            variant="text"
            :disabled="!editable"
            @click="form.ai_prompt = defaultPrompt"
          >
            既定に戻す
          </v-btn>
        </div>
        <HelpTip :text="HELP.ai_prompt">
          <template #default="{ props }">
            <v-textarea
              v-bind="props"
              v-model="form.ai_prompt"
              data-test="ai-prompt"
              label="プロンプト（編集できる部分）"
              :disabled="!editable"
              :error-messages="errors.ai_prompt"
              :counter="8000"
              :counter-value="(value: string) => [...value].length"
              rows="6"
              auto-grow
              variant="outlined"
              :hint="HELP.ai_prompt"
              persistent-hint
            />
          </template>
        </HelpTip>
        <v-sheet
          v-if="promptFixed"
          data-test="ai-prompt-fixed"
          border
          rounded
          class="pa-3 mt-3"
        >
          <HelpTip :text="HELP.ai_prompt_fixed">
            <template #default="{ props }">
              <p
                v-bind="props"
                class="text-subtitle-2 mb-2"
              >
                固定部分（編集不可。保存時にプログラムが結合します）
              </p>
            </template>
          </HelpTip>
          <p class="fixed-text text-body-2 mb-2">
            system prompt の末尾: {{ promptFixed.system_suffix }}
          </p>
          <p class="fixed-text text-body-2">
            データの前置き: {{ promptFixed.data_prefix }}
          </p>
        </v-sheet>
        <PromptPresetCards
          v-model:presets="form.ai_prompt_presets"
          :current-prompt="form.ai_prompt"
          :default-prompt="defaultPrompt"
          :editable="editable"
          @apply="form.ai_prompt = $event"
        />
        <h3 class="text-subtitle-1 mt-4 mb-3">
          過去の日で試す
        </h3>
        <p class="text-body-2 mb-3">
          未保存の文面・系統・ピン留めで試せます。結果を確認してから設定を保存してください。AI 判定が無効でも試せます。
        </p>
        <v-alert
          v-if="daysStore.error"
          type="warning"
          variant="tonal"
          class="mb-3"
        >
          {{ daysStore.error }}<v-btn
            variant="text"
            :disabled="!isAdmin"
            @click="daysStore.fetch()"
          >
            再取得
          </v-btn>
        </v-alert>
        <div class="d-flex align-start flex-wrap ga-3">
          <v-select
            v-model="previewDate"
            data-test="preview-date"
            label="対象日"
            :items="previewDays"
            :disabled="!editable || preview.busy.value"
            :loading="daysStore.loading"
            :hint="!daysStore.loading && !previewDays.length ? '集計が成功したレポートが必要です' : ''"
            persistent-hint
            variant="outlined"
            style="max-width: 300px"
          />
          <HelpTip :text="HELP.preview">
            <template #default="{ props }">
              <span v-bind="props"><v-btn
                data-test="preview-start"
                color="primary"
                variant="tonal"
                :disabled="!canPreview"
                :loading="preview.busy.value"
                @click="preview.start(previewRequest)"
              >この文面で試す</v-btn></span>
            </template>
          </HelpTip>
        </div>
        <div
          v-if="preview.busy.value || preview.error.value || preview.result.value"
          data-test="preview-panel"
          class="mt-3"
          role="status"
          aria-live="polite"
        >
          <v-chip
            v-if="previewChanged"
            data-test="preview-stale"
            color="warning"
            size="small"
            class="mb-3"
          >
            変更前の試行
          </v-chip>
          <p v-if="preview.busy.value">
            AI 判定を試行中です（最大 180 秒）。
          </p>
          <v-alert
            v-if="preview.error.value"
            type="error"
            variant="tonal"
          >
            {{ preview.error.value }}
          </v-alert>
          <template v-if="preview.result.value?.status === 'succeeded' && preview.result.value.result">
            <h3 class="text-subtitle-1">
              試行結果 <LevelChip :level="preview.result.value.result.level" />
            </h3>
            <div class="table-scroll">
              <v-table
                density="compact"
                aria-label="試行の区分判定"
              >
                <thead><tr><th>区分</th><th>段階</th><th>理由</th></tr></thead><tbody>
                  <tr
                    v-for="section in preview.result.value.result.sections"
                    :key="section.section"
                  >
                    <td>{{ sectionLabel(section.section) }}</td><td><LevelChip :level="preview.result.value.result.levels?.[section.section] ?? section.level" /></td><td>{{ section.reason }}</td>
                  </tr>
                </tbody>
              </v-table>
            </div>
            <p class="text-subtitle-2 mt-3">
              AI の見解
            </p><p class="preview-opinion">
              {{ preview.result.value.result.opinion }}
            </p>
            <p class="text-body-2 mt-3">
              使用モデル: {{ preview.result.value.result.model_label || '—' }}（{{ preview.result.value.result.model_id || '—' }}）
            </p>
            <p class="text-caption">
              入力トークン: {{ preview.result.value.result.input_tokens ?? '—' }} / 出力トークン: {{ preview.result.value.result.output_tokens ?? '—' }} / 所要: {{ preview.result.value.result.duration_sec ?? '—' }} 秒
            </p>
            <p
              v-if="preview.result.value.result.note"
              class="text-body-2"
            >
              {{ preview.result.value.result.note }}
            </p>
          </template>
        </div>
      </v-card-text>
    </v-card>
    <v-card
      variant="flat"
      border
      class="mb-4"
    >
      <v-card-text>
        <v-row>
          <v-col
            v-for="toggle in toggles"
            :key="toggle.key"
            cols="12"
            md="6"
          >
            <HelpTip :text="HELP[toggle.key]">
              <template #default="{ props }">
                <v-switch
                  v-bind="props"
                  v-model="form[toggle.key]"
                  :data-test="toggle.key.replaceAll('_', '-')"
                  :label="toggle.label"
                  :disabled="!editable"
                  color="primary"
                  hide-details
                />
              </template>
            </HelpTip>
          </v-col>
        </v-row>
        <h2 class="text-subtitle-1 mt-6 mb-2">
          監視する区分
        </h2>
        <v-row>
          <v-col
            v-for="section in SECTION_FIELDS"
            :key="section.key"
            cols="12"
            md="6"
          >
            <HelpTip :text="HELP.sections">
              <template #default="{ props }">
                <v-switch
                  v-bind="props"
                  v-model="form.sections[section.key]"
                  :data-test="`section-${section.key}`"
                  :label="section.label"
                  :hint="section.hint"
                  persistent-hint
                  :disabled="!editable"
                  color="primary"
                />
              </template>
            </HelpTip>
          </v-col>
        </v-row>
        <h2 class="text-subtitle-1 mt-6 mb-4">
          通知先
        </h2>
        <v-row>
          <v-col
            v-for="field in mailFields"
            :key="field.key"
            cols="12"
            md="6"
            @focusout="commitPending($event, field.key)"
          >
            <HelpTip :text="HELP[field.key]">
              <template #default="{ props }">
                <v-combobox
                  v-bind="props"
                  v-model="form[field.key]"
                  v-model:search="pendingMail[field.key]"
                  :data-test="field.key.replaceAll('_', '-')"
                  :label="field.label"
                  multiple
                  chips
                  closable-chips
                  variant="outlined"
                  density="comfortable"
                  hint="入力して Enter で追加します（@example.co.jp のみ）"
                  persistent-hint
                  :disabled="!editable"
                  :error-messages="errors[field.key]"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col
            v-if="isAdmin && store.directoryError"
            cols="12"
          >
            <v-alert
              data-test="slack-directory-error"
              type="warning"
              variant="tonal"
            >
              Slack の候補を取得できませんでした: {{ store.directoryError }}
              <HelpTip :text="HELP.directory">
                <template #default="{ props }">
                  <v-btn
                    v-bind="props"
                    data-test="slack-directory-retry"
                    variant="text"
                    :loading="store.directoryLoading"
                    @click="store.loadSlackDirectory(true)"
                  >
                    再取得
                  </v-btn>
                </template>
              </HelpTip>
            </v-alert>
          </v-col>
          <v-col
            cols="12"
            md="6"
          >
            <HelpTip :text="HELP.slackChannel">
              <template #default="{ props }">
                <TriggerSuggestField
                  v-bind="props"
                  v-model="slackChannelValues"
                  data-test="slack-channel-id"
                  trigger="#"
                  label="Slack チャンネル"
                  placeholder="「#」を入力するとチャンネル候補が出ます"
                  :items="channelItems"
                  :fallback-label="channelFallbackLabel"
                  :disabled="!editable"
                  :loading="store.directoryLoading"
                  :error-messages="errors.slack_channel_id ? [errors.slack_channel_id] : []"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col
            cols="12"
            md="6"
          >
            <HelpTip :text="HELP.slackMention">
              <template #default="{ props }">
                <TriggerSuggestField
                  v-bind="props"
                  v-model="slackMentionValues"
                  data-test="slack-mention"
                  trigger="@"
                  label="Slack メンション"
                  placeholder="「@」を入力するとメンション候補が出ます"
                  multiple
                  :items="mentionItems"
                  :fallback-label="mentionFallbackLabel"
                  :disabled="!editable"
                  :loading="store.directoryLoading"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col
            cols="12"
            md="6"
          >
            <HelpTip :text="HELP.slackMentionLevel">
              <template #default="{ props }">
                <v-select
                  v-bind="props"
                  v-model="form.slack_mention_level"
                  data-test="slack-mention-level"
                  label="メンションを付ける段階"
                  :items="MENTION_LEVELS"
                  :disabled="!editable"
                  :error-messages="errors.slack_mention_level"
                  hint="即時通知（GuardDuty 重大検出）は Critical 扱いで常に付きます。メールには付けません"
                  persistent-hint
                  variant="outlined"
                />
              </template>
            </HelpTip>
          </v-col>
        </v-row>
        <h2 class="text-subtitle-1 mt-6 mb-4">
          しきい値
        </h2>
        <v-row>
          <v-col
            v-for="field in THRESHOLD_FIELDS"
            :key="field.key"
            cols="12"
            sm="6"
            md="3"
          >
            <HelpTip :text="HELP[field.key]">
              <template #default="{ props }">
                <v-text-field
                  v-bind="props"
                  v-model.number="form[field.key]"
                  :data-test="field.key.replaceAll('_', '-')"
                  :label="field.label"
                  type="number"
                  :min="field.min"
                  :max="'max' in field ? field.max : undefined"
                  :step="field.step"
                  variant="outlined"
                  density="comfortable"
                  :disabled="!editable"
                  :error-messages="errors[field.key]"
                />
              </template>
            </HelpTip>
          </v-col>
        </v-row>
        <HelpTip :text="HELP.inventory_weekday">
          <template #default="{ props }">
            <v-select
              v-bind="props"
              v-model="form.inventory_weekday"
              data-test="inventory-weekday"
              label="棚卸しの曜日"
              :items="weekdays"
              :disabled="!editable"
              :error-messages="errors.inventory_weekday"
              variant="outlined"
              class="mt-3"
            />
          </template>
        </HelpTip>
        <HelpTip :text="HELP.metricTargets">
          <template #default="{ props }">
            <v-textarea
              v-bind="props"
              v-model="metricTargetsText"
              data-test="metric-targets"
              label="指標の集計対象（metric_targets）"
              :hint="HELP.metricTargets"
              persistent-hint
              rows="5"
              auto-grow
              variant="outlined"
              :disabled="!editable"
              :error-messages="errors.metric_targets"
              class="mt-4"
            />
          </template>
        </HelpTip>
        <HelpTip :text="HELP.adminPath">
          <template #default="{ props }">
            <v-text-field
              v-bind="props"
              v-model="form.alarmhuba_admin_path_regex"
              data-test="alarmhuba-admin-path-regex"
              label="alarmhuba 管理 API のパス（正規表現）"
              variant="outlined"
              :disabled="!editable"
              class="mt-4"
            />
          </template>
        </HelpTip>
        <div class="d-flex align-center flex-wrap ga-2 mt-6 mb-3">
          <HelpTip :text="HELP.exclusions">
            <template #default="{ props }">
              <h2
                v-bind="props"
                class="text-subtitle-1"
              >
                除外リスト（{{ form.exclusions.length }} / 200 件）
              </h2>
            </template>
          </HelpTip>
          <v-spacer />
          <v-btn
            v-if="isAdmin"
            data-test="exclusion-add"
            prepend-icon="mdi-plus"
            variant="tonal"
            :disabled="!editable || form.exclusions.length >= 200"
            @click="addExclusion"
          >
            行を追加
          </v-btn>
        </div>
        <div class="table-scroll">
          <v-table class="exclusion-table">
            <thead>
              <tr>
                <th
                  v-for="column in exclusionColumns"
                  :key="column.key"
                >
                  <HelpTip :text="HELP[column.key]">
                    <template #default="{ props }">
                      <span v-bind="props">{{ column.label }}</span>
                    </template>
                  </HelpTip>
                </th>
                <th v-if="isAdmin">
                  操作
                </th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(row, index) in form.exclusions"
                :key="index"
                data-test="exclusion-row"
              >
                <td>
                  <v-select
                    v-model="row.kind"
                    :data-test="`exclusion-${index}-kind`"
                    :label="`${index + 1} 行目 種類`"
                    :items="kinds"
                    :disabled="!editable"
                    density="compact"
                    variant="outlined"
                    hide-details
                  />
                </td>
                <td>
                  <v-text-field
                    v-model="row.value"
                    :data-test="`exclusion-${index}-value`"
                    :label="`${index + 1} 行目 値`"
                    :disabled="!editable"
                    :error-messages="errors[`exclusions.${index}.value`]"
                    density="compact"
                    variant="outlined"
                  />
                </td>
                <td>
                  <v-select
                    v-model="row.scope"
                    :data-test="`exclusion-${index}-scope`"
                    :label="`${index + 1} 行目 適用先`"
                    :items="scopes"
                    multiple
                    chips
                    :disabled="!editable"
                    :error-messages="errors[`exclusions.${index}.scope`]"
                    density="compact"
                    variant="outlined"
                  />
                </td>
                <td>
                  <v-select
                    v-model="row.category"
                    :data-test="`exclusion-${index}-category`"
                    :label="`${index + 1} 行目 区分`"
                    :items="[{ title: '通常', value: '' }, { title: '社内（office）', value: 'office' }]"
                    :disabled="!editable"
                    density="compact"
                    variant="outlined"
                    hide-details
                  />
                </td>
                <td>
                  <v-text-field
                    v-model="row.reason"
                    :data-test="`exclusion-${index}-reason`"
                    :label="`${index + 1} 行目 理由`"
                    :disabled="!editable"
                    :error-messages="errors[`exclusions.${index}.reason`]"
                    density="compact"
                    variant="outlined"
                  />
                </td>
                <td>
                  <v-text-field
                    v-model="row.expires_at"
                    :data-test="`exclusion-${index}-expires-at`"
                    :label="`${index + 1} 行目 期限`"
                    type="date"
                    :disabled="!editable"
                    :error-messages="errors[`exclusions.${index}.expires_at`]"
                    density="compact"
                    variant="outlined"
                  />
                  <v-chip
                    v-if="exclusionExpired(row.expires_at)"
                    size="small"
                    color="warning"
                    class="mb-2"
                  >
                    期限切れ
                  </v-chip>
                </td>
                <td v-if="isAdmin">
                  <v-btn
                    :data-test="`exclusion-${index}-delete`"
                    :aria-label="`${index + 1} 行目を削除`"
                    icon="mdi-delete-outline"
                    variant="text"
                    color="error"
                    :disabled="!editable"
                    @click="deleteExclusion(index)"
                  />
                </td>
              </tr>
              <tr v-if="!form.exclusions.length">
                <td
                  :colspan="isAdmin ? 7 : 6"
                  class="text-center text-medium-emphasis py-6"
                >
                  除外は登録されていません
                </td>
              </tr>
            </tbody>
          </v-table>
        </div>
        <v-alert
          v-if="Object.keys(errors).length"
          data-test="validation-errors"
          type="warning"
          variant="tonal"
          class="mt-4"
        >
          入力エラーがあるため保存できません。
          <ul class="pl-5">
            <li
              v-for="(message, key) in errors"
              :key="key"
            >
              {{ message }}
            </li>
          </ul>
        </v-alert>
        <div
          v-if="store.settings"
          class="text-caption text-medium-emphasis mt-4"
        >
          最終更新 {{ formatDateTime(store.settings.updated_at) }} / {{ store.settings.updated_by_email || '—' }}
        </div>
      </v-card-text>
      <v-card-actions v-if="isAdmin">
        <v-chip
          v-if="isDirty"
          data-test="dirty-chip"
          color="warning"
          size="small"
        >
          未保存の変更があります
        </v-chip>
        <HelpTip
          v-if="isDirty"
          :text="HELP.discard"
        >
          <template #default="{ props }">
            <v-btn
              v-bind="props"
              data-test="discard-changes"
              :disabled="store.loading"
              @click="discardChanges"
            >
              変更を破棄
            </v-btn>
          </template>
        </HelpTip>
        <v-spacer />
        <HelpTip :text="HELP.save">
          <template #default="{ props }">
            <span v-bind="props"><v-btn
              data-test="settings-save"
              color="primary"
              variant="flat"
              :disabled="!canSave"
              :loading="store.loading"
              @click="save"
            >保存</v-btn></span>
          </template>
        </HelpTip>
      </v-card-actions>
    </v-card>
    <v-dialog
      v-model="leaveDialog"
      max-width="440"
      persistent
    >
      <v-card data-test="leave-dialog">
        <v-card-title>未保存の変更があります</v-card-title>
        <v-card-text>この画面を離れると、保存していない変更は失われます。</v-card-text>
        <v-card-actions>
          <v-btn
            data-test="leave-stay"
            @click="answerLeave(false)"
          >
            このまま編集を続ける
          </v-btn>
          <v-btn
            data-test="leave-discard"
            color="error"
            @click="answerLeave(true)"
          >
            破棄して移動する
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.preview-opinion { white-space: pre-wrap; overflow-wrap: anywhere; }
.fixed-text { white-space: pre-line; overflow-wrap: anywhere; }
.table-scroll { overflow-x: auto; }
.exclusion-table { min-width: 1280px; }
.exclusion-table td { min-width: 150px; padding-top: 12px; padding-bottom: 12px; }
.exclusion-table td:nth-child(2), .exclusion-table td:nth-child(5) { min-width: 240px; }
.exclusion-table td:nth-child(3) { min-width: 220px; }
</style>
