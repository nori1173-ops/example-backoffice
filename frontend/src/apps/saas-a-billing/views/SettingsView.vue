<script setup lang="ts">
import { computed, onActivated, onDeactivated, onUnmounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import { formatDateTime } from '@/utils/format'
import HelpTip from '@/components/HelpTip.vue'
import TriggerSuggestField, { type SuggestItem } from '@/components/TriggerSuggestField.vue'
import EnvBadge from '../components/EnvBadge.vue'
import PriceTableEditor from '../components/PriceTableEditor.vue'
import { HELP } from '../help'
import { useSettingsStore } from '../stores/settings'
import type { MailAttachmentSheets, PriceTable, SettingsView, TargetEnv } from '../types'
import { joinMentions, mentionFallbackLabel, parseMentions } from '../utils/slackMention'

interface SettingsForm {
  cutoff_day: string
  cutoff_time: string
  auto_run_enabled: boolean
  auto_run_from_month: string
  mail_enabled: boolean
  slack_enabled: boolean
  mail_to: string[]
  mail_cc: string[]
  mail_attachment_sheets: MailAttachmentSheets
  slack_channel_id: string
  slack_channel_label: string
  slack_mention: string
  slack_template: string
  exclude_keywords: string[]
  camera_format_names: string[]
}

const CUTOFF_DAY_MIN = 1
const CUTOFF_DAY_MAX = 28
const INTEGER_PATTERN = /^\d+$/
const TIME_PATTERN = /^(?:[01]\d|2[0-3]):[0-5]\d$/
const END_OF_DAY = '24:00'
const MONTH_PATTERN = /^\d{4}-(?:0[1-9]|1[0-2])$/
const SLACK_CHANNEL_PATTERN = /^[CG][A-Za-z0-9]+$/
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/
const PLACEHOLDER_PATTERN = /\{[^}]*\}/g
const TARGET_ENV_LABELS: Record<TargetEnv, string> = {
  verification: '検証環境',
  production: '本番',
}
const ALLOWED_PLACEHOLDERS = [
  '{month}',
  '{period}',
  '{tenants}',
  '{devices}',
  '{billable}',
  '{total}',
  '{url}',
]
/** ユーザー候補より前に出す特別なメンション（#1011）。 */
const MENTION_SPECIALS: SuggestItem[] = [
  { value: '<!here>', label: '@here', hint: 'オンライン全員' },
  { value: '<!channel>', label: '@channel', hint: 'チャンネル全員' },
]
/** v-combobox multiple は Enter か blur までチップにならない（UI-11）。 */
type ListField = 'mail_to' | 'mail_cc' | 'exclude_keywords' | 'camera_format_names'

const auth = useAuthStore()
const store = useSettingsStore()
const ui = useUiStore()
const isAdmin = computed(() => auth.groups.includes('admin'))
const snapshot = ref('')
const leaveDialog = ref(false)
let leaveResolve: ((leave: boolean) => void) | null = null

const form = reactive<SettingsForm>({
  cutoff_day: '',
  cutoff_time: '',
  auto_run_enabled: false,
  auto_run_from_month: '',
  mail_enabled: false,
  slack_enabled: false,
  mail_to: [],
  mail_cc: [],
  mail_attachment_sheets: 'billing_only',
  slack_channel_id: '',
  slack_channel_label: '',
  slack_mention: '',
  slack_template: '',
  exclude_keywords: [],
  camera_format_names: [],
})

// 空フォームを初期スナップショットにする。未取得を「未保存の編集」と誤判定すると再取得を飛ばす
snapshot.value = JSON.stringify(form)

function seed(settings: SettingsView | null): void {
  if (!settings) {
    return
  }
  form.cutoff_day = String(settings.cutoff_day)
  form.cutoff_time = settings.cutoff_time
  form.auto_run_enabled = settings.auto_run_enabled
  form.auto_run_from_month = settings.auto_run_from_month
  form.mail_enabled = settings.mail_enabled
  form.slack_enabled = settings.slack_enabled
  form.mail_to = [...settings.mail_to]
  form.mail_cc = [...settings.mail_cc]
  form.mail_attachment_sheets = settings.mail_attachment_sheets ?? 'billing_only'
  form.slack_channel_id = settings.slack_channel_id
  form.slack_channel_label = settings.slack_channel_label
  form.slack_mention = settings.slack_mention
  form.slack_template = settings.slack_template
  form.exclude_keywords = [...settings.exclude_keywords]
  form.camera_format_names = [...settings.camera_format_names]
  snapshot.value = JSON.stringify(form)
}

/**
 * 入力途中の値を捨てずに確定する（UI-11）。
 * 入力欄の実値を見るので、打ちかけを消してから離れたときは何も足さない。
 */
function commitPending(event: FocusEvent, field: ListField): void {
  const value = (event.target as HTMLInputElement | null)?.value?.trim() ?? ''
  if (!value || form[field].includes(value)) {
    return
  }
  form[field] = [...form[field], value]
}

const isDirty = computed(() => JSON.stringify(form) !== snapshot.value)

function inRange(value: string, min: number, max: number): boolean {
  return INTEGER_PATTERN.test(value) && Number(value) >= min && Number(value) <= max
}

function invalidEmails(addresses: string[]): string[] {
  return addresses.filter((address) => !EMAIL_PATTERN.test(address))
}

function unknownPlaceholders(template: string): string[] {
  return (template.match(PLACEHOLDER_PATTERN) ?? []).filter(
    (placeholder) => !ALLOWED_PLACEHOLDERS.includes(placeholder),
  )
}

/** チャンネル ID は Slack 配布が ON のときだけ必須。非空なら常に形式を見る（バックエンドと同条件）。 */
function slackChannelErrors(): string[] {
  if (!form.slack_channel_id) {
    return form.slack_enabled ? ['Slack 配布を有効にするにはチャンネル ID が必要です'] : []
  }
  return SLACK_CHANNEL_PATTERN.test(form.slack_channel_id)
    ? []
    : ['保存済みの値が古い形式です。候補から選び直してください']
}

/** 宛先はメール配布が ON のときだけ必須。項目のメール形式は常に見る（バックエンドと同条件）。 */
function mailToErrors(): string[] {
  const bad = invalidEmails(form.mail_to)
  if (bad.length) {
    return [`メール形式ではありません: ${bad.join(', ')}`]
  }
  return form.mail_enabled && !form.mail_to.length
    ? ['メール配布を有効にするには宛先が必要です']
    : []
}

/** 入力の妥当性は UX 目的で、実体の検証はバックエンド（設計 8 章の `PUT /settings`）。 */
const errors = computed(() => {
  const badCc = invalidEmails(form.mail_cc)
  const badPlaceholders = unknownPlaceholders(form.slack_template)
  return {
    cutoff_day: inRange(form.cutoff_day, CUTOFF_DAY_MIN, CUTOFF_DAY_MAX)
      ? []
      : [`締め日は ${CUTOFF_DAY_MIN}〜${CUTOFF_DAY_MAX} で指定してください`],
    cutoff_time:
      form.cutoff_time === END_OF_DAY || TIME_PATTERN.test(form.cutoff_time)
        ? []
        : ['締め時刻は HH:MM 形式で指定してください（24:00 可）'],
    auto_run_from_month: MONTH_PATTERN.test(form.auto_run_from_month)
      ? []
      : ['自動実行の開始月は YYYY-MM 形式で指定してください'],
    slack_channel_id: slackChannelErrors(),
    mail_to: mailToErrors(),
    mail_cc: badCc.length ? [`メール形式ではありません: ${badCc.join(', ')}`] : [],
    slack_template: badPlaceholders.length
      ? [`使えないプレースホルダです: ${badPlaceholders.join(', ')}`]
      : [],
  }
})

const hasError = computed(() => Object.values(errors.value).some((messages) => messages.length))

const channelItems = computed<SuggestItem[]>(() =>
  (store.directory?.channels ?? []).map((channel) => ({
    value: channel.id,
    label: `#${channel.name}`,
    hint: channel.is_private ? '非公開' : undefined,
    keywords: [channel.name],
  })),
)

const mentionItems = computed<SuggestItem[]>(() => [
  ...MENTION_SPECIALS,
  ...(store.directory?.users ?? []).map((user) => ({
    value: `<@${user.id}>`,
    label: `@${user.display_name || user.real_name}`,
    hint: `${user.real_name} · @${user.handle}`,
    keywords: [user.handle, user.real_name, user.display_name],
  })),
])

/** チャンネルは 1 件だけ。選んだときに表示名メモ（`slack_channel_label`）も揃える。 */
const slackChannelValues = computed({
  get: () => (form.slack_channel_id ? [form.slack_channel_id] : []),
  set: (values: string[]) => {
    const id = values[0] ?? ''
    const item = channelItems.value.find((candidate) => candidate.value === id)
    form.slack_channel_id = id
    form.slack_channel_label = id ? (item?.label ?? form.slack_channel_label) : ''
  },
})

/** 保存形式は空白区切りの文字列のまま。画面ではトークンの配列として扱う（#1011）。 */
const slackMentionValues = computed({
  get: () => parseMentions(form.slack_mention),
  set: (values: string[]) => {
    form.slack_mention = joinMentions(values)
  },
})

/** 候補に無いチャンネルは保存済みの表示名で見せる。それも無いときだけ ID を借りる。 */
function channelFallbackLabel(id: string): string {
  return form.slack_channel_label || `#${id}`
}

// 集計対象環境はサイトごとに固定で、画面からは変更できない（設計 R1 / UI-08）
const fixedEnv = computed<TargetEnv | null>(() => store.settings?.target_env ?? null)
const fixedEnvLabel = computed(() =>
  fixedEnv.value ? TARGET_ENV_LABELS[fixedEnv.value] : '未取得',
)
const targetReady = computed(() => !!fixedEnv.value && !!store.targets[fixedEnv.value])

const canSave = computed(
  () => isAdmin.value && !hasError.value && targetReady.value && !store.loading,
)

const targetEnvHint = computed(() =>
  fixedEnv.value && !targetReady.value
    ? `${fixedEnvLabel.value}の接続情報（SSM）が未登録です。管理者が SSM に登録すると保存・実行ができます。`
    : '',
)

const saveBlockedReason = computed(() => {
  if (!targetReady.value) {
    return `${fixedEnvLabel.value}の接続情報（SSM）が未登録のため保存できません`
  }
  if (hasError.value) {
    return '入力エラーがあるため保存できません'
  }
  return ''
})

async function save(): Promise<void> {
  const ok = await store.save({
    ...form,
    cutoff_day: Number(form.cutoff_day),
  })
  if (ok) {
    ui.notify('設定を保存しました', 'success')
  } else {
    ui.notify(store.error ?? '設定の保存に失敗しました', 'error')
  }
}

async function addPrice(price: PriceTable): Promise<void> {
  const ok = await store.addPrice(price)
  if (ok) {
    ui.notify('単価の改定を登録しました', 'success')
  } else {
    ui.notify(store.error ?? '単価表の追加に失敗しました', 'error')
  }
}

function answerLeave(leave: boolean): void {
  leaveDialog.value = false
  if (leave) {
    seed(store.settings)
  }
  leaveResolve?.(leave)
  leaveResolve = null
}

/** 編集途中の離脱で黙って消えないようにする（UI-09）。 */
onBeforeRouteLeave(async () => {
  if (!isDirty.value) {
    return true
  }
  leaveDialog.value = true
  return await new Promise<boolean>((resolve) => {
    leaveResolve = resolve
  })
})

function warnUnload(event: BeforeUnloadEvent): void {
  if (isDirty.value) {
    event.preventDefault()
  }
}

watch(() => store.settings, seed, { immediate: true })

// 編集中でなければ画面に入るたびに取り直す（他の人の更新を古い値で上書きしないため）
onActivated(async () => {
  window.addEventListener('beforeunload', warnUnload)
  // 候補は admin 専用の API。閲覧者に権限エラーの警告を出さず、取得済みなら往復も省く（#1011）
  if (isAdmin.value && !store.directory) {
    void store.loadSlackDirectory()
  }
  if (store.settings && isDirty.value) {
    return
  }
  await store.fetch()
})

onDeactivated(() => window.removeEventListener('beforeunload', warnUnload))
onUnmounted(() => window.removeEventListener('beforeunload', warnUnload))
</script>

<template>
  <div>
    <div class="mb-4">
      <h1 class="text-h5 mb-1">
        設定
      </h1>
      <p class="text-body-2 text-medium-emphasis mb-0">
        月額課金レポートの締め日時・配布先・単価表を管理します。
      </p>
    </div>

    <v-card
      variant="flat"
      border
      class="mb-4"
    >
      <v-card-text data-test="target-summary">
        <div class="d-flex align-center flex-wrap ga-4">
          <div
            data-test="target-env-fixed"
            class="d-flex align-center ga-2"
          >
            <span class="text-body-2 text-medium-emphasis">集計対象環境</span>
            <EnvBadge
              v-if="fixedEnv"
              :target-env="fixedEnv"
            />
            <span class="text-caption text-medium-emphasis">
              このサイトでは固定です（画面からは変更できません）
            </span>
          </div>
          <div class="text-body-2">
            <span class="text-medium-emphasis me-2">接続先ホスト</span>
            <span class="font-weight-medium">{{ store.saasaHost ?? '未登録' }}</span>
          </div>
          <div class="text-body-2">
            <span class="text-medium-emphasis me-2">Slack の動作方式</span>
            <span
              data-test="slack-mode"
              class="font-weight-medium"
            >
              {{ store.slackMode === 'webhook' ? 'Webhook フォールバック' : 'Bot token' }}
            </span>
          </div>
        </div>
        <div
          v-if="targetEnvHint"
          data-test="target-env-hint"
          class="text-caption text-warning mt-2"
        >
          {{ targetEnvHint }}
        </div>
      </v-card-text>
    </v-card>

    <v-alert
      v-if="store.slackMode === 'webhook'"
      data-test="webhook-notice"
      type="warning"
      variant="tonal"
      class="mb-4"
    >
      Slack が Webhook フォールバックで動作しているため、チャンネル ID の設定は無視され、Webhook
      に紐づくチャンネルへ投稿されます。
    </v-alert>

    <v-alert
      v-if="!isAdmin"
      data-test="readonly-notice"
      type="info"
      variant="tonal"
      class="mb-4"
    >
      設定の変更は admin グループのみが行えます。この画面は読み取り専用です。
    </v-alert>

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
      class="mb-4"
    >
      <v-card-text>
        <v-row dense>
          <v-col
            cols="12"
            sm="4"
          >
            <HelpTip :text="HELP.settings.cutoff">
              <template #default="{ props }">
                <v-text-field
                  v-bind="props"
                  v-model="form.cutoff_day"
                  data-test="cutoff-day"
                  label="締め日（毎月）"
                  density="comfortable"
                  variant="outlined"
                  :disabled="!isAdmin"
                  :error-messages="errors.cutoff_day"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col
            cols="12"
            sm="4"
          >
            <HelpTip :text="HELP.settings.cutoff">
              <template #default="{ props }">
                <v-text-field
                  v-bind="props"
                  v-model="form.cutoff_time"
                  data-test="cutoff-time"
                  label="締め時刻"
                  hint="24:00 はその日の終わりまで含む"
                  persistent-hint
                  density="comfortable"
                  variant="outlined"
                  :disabled="!isAdmin"
                  :error-messages="errors.cutoff_time"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col
            cols="12"
            sm="4"
          >
            <HelpTip :text="HELP.settings.autoRun">
              <template #default="{ props }">
                <v-text-field
                  v-bind="props"
                  v-model="form.auto_run_from_month"
                  data-test="auto-run-from-month"
                  label="自動実行の開始月"
                  placeholder="YYYY-MM"
                  density="comfortable"
                  variant="outlined"
                  :disabled="!isAdmin"
                  :error-messages="errors.auto_run_from_month"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col
            cols="12"
            sm="4"
            class="d-flex align-center"
          >
            <HelpTip :text="HELP.settings.autoRun">
              <template #default="{ props }">
                <v-switch
                  v-bind="props"
                  v-model="form.auto_run_enabled"
                  data-test="auto-run-enabled"
                  label="自動実行を有効にする"
                  color="primary"
                  density="comfortable"
                  hide-details
                  :disabled="!isAdmin"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col
            cols="12"
            sm="4"
            class="d-flex align-center"
          >
            <div class="text-body-2">
              <span class="text-medium-emphasis me-2">実行時刻</span>
              <span class="font-weight-medium">06:00 JST 固定</span>
            </div>
          </v-col>
        </v-row>

        <v-divider class="my-4" />

        <v-row dense>
          <v-col cols="12">
            <HelpTip :text="HELP.settings.mailEnabled">
              <template #default="{ props }">
                <v-switch
                  v-bind="props"
                  v-model="form.mail_enabled"
                  data-test="mail-enabled"
                  label="メール配布を有効にする"
                  hint="OFF のときは宛先を空にできます"
                  persistent-hint
                  color="primary"
                  density="comfortable"
                  :disabled="!isAdmin"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col
            cols="12"
            sm="6"
            @focusout="commitPending($event, 'mail_to')"
          >
            <HelpTip :text="HELP.settings.mailTo">
              <template #default="{ props }">
                <v-combobox
                  v-bind="props"
                  v-model="form.mail_to"
                  data-test="mail-to"
                  label="メール宛先 To"
                  hint="入力して Enter で追加します"
                  persistent-hint
                  multiple
                  chips
                  closable-chips
                  density="comfortable"
                  variant="outlined"
                  :disabled="!isAdmin"
                  :error-messages="errors.mail_to"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col
            cols="12"
            sm="6"
            @focusout="commitPending($event, 'mail_cc')"
          >
            <HelpTip :text="HELP.settings.mailCc">
              <template #default="{ props }">
                <v-combobox
                  v-bind="props"
                  v-model="form.mail_cc"
                  data-test="mail-cc"
                  label="メール宛先 Cc"
                  hint="入力して Enter で追加します"
                  persistent-hint
                  multiple
                  chips
                  closable-chips
                  density="comfortable"
                  variant="outlined"
                  :disabled="!isAdmin"
                  :error-messages="errors.mail_cc"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col
            cols="12"
            sm="6"
          >
            <HelpTip :text="HELP.settings.mailAttachmentSheets">
              <template #default="{ props }">
                <v-select
                  v-bind="props"
                  v-model="form.mail_attachment_sheets"
                  data-test="mail-attachment-sheets"
                  label="メールに添付する Excel"
                  :items="[
                    { title: 'シート 1（請求照合）のみ', value: 'billing_only' },
                    { title: '両方のシート（請求照合 + 全地点）', value: 'all' },
                  ]"
                  density="comfortable"
                  variant="outlined"
                  :disabled="!isAdmin"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col cols="12">
            <HelpTip :text="HELP.settings.slackEnabled">
              <template #default="{ props }">
                <v-switch
                  v-bind="props"
                  v-model="form.slack_enabled"
                  data-test="slack-enabled"
                  label="Slack 配布を有効にする"
                  hint="OFF のときはチャンネル ID を空にできます"
                  persistent-hint
                  color="primary"
                  density="comfortable"
                  :disabled="!isAdmin"
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
              density="compact"
            >
              <div class="d-flex align-center flex-wrap ga-2">
                <span>Slack の候補を取得できませんでした: {{ store.directoryError }}</span>
                <HelpTip :text="HELP.settings.slackDirectoryRetry">
                  <template #default="{ props }">
                    <v-btn
                      v-bind="props"
                      data-test="slack-directory-retry"
                      size="small"
                      variant="text"
                      @click="store.loadSlackDirectory(true)"
                    >
                      再取得
                    </v-btn>
                  </template>
                </HelpTip>
              </div>
            </v-alert>
          </v-col>
          <v-col
            cols="12"
            sm="6"
          >
            <HelpTip :text="HELP.settings.slackChannel">
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
                  :disabled="!isAdmin"
                  :loading="store.directoryLoading"
                  :error-messages="errors.slack_channel_id"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col
            cols="12"
            sm="6"
          >
            <HelpTip :text="HELP.settings.slackMention">
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
                  :disabled="!isAdmin"
                  :loading="store.directoryLoading"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col cols="12">
            <HelpTip :text="HELP.settings.slackTemplate">
              <template #default="{ props }">
                <v-textarea
                  v-bind="props"
                  v-model="form.slack_template"
                  data-test="slack-template"
                  label="Slack 文面"
                  rows="3"
                  auto-grow
                  density="comfortable"
                  variant="outlined"
                  :disabled="!isAdmin"
                  :error-messages="errors.slack_template"
                  :hint="`使えるプレースホルダ: ${ALLOWED_PLACEHOLDERS.join(' ')}`"
                  persistent-hint
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col
            cols="12"
            sm="6"
            @focusout="commitPending($event, 'exclude_keywords')"
          >
            <HelpTip :text="HELP.settings.excludeKeywords">
              <template #default="{ props }">
                <v-combobox
                  v-bind="props"
                  v-model="form.exclude_keywords"
                  data-test="exclude-keywords"
                  label="除外キーワード"
                  hint="入力して Enter で追加します（テナント説明欄・地点備考欄に含まれたら課金対象外）"
                  persistent-hint
                  multiple
                  chips
                  closable-chips
                  density="comfortable"
                  variant="outlined"
                  :disabled="!isAdmin"
                />
              </template>
            </HelpTip>
          </v-col>
          <v-col
            cols="12"
            sm="6"
            @focusout="commitPending($event, 'camera_format_names')"
          >
            <HelpTip :text="HELP.settings.cameraFormats">
              <template #default="{ props }">
                <v-combobox
                  v-bind="props"
                  v-model="form.camera_format_names"
                  data-test="camera-format-names"
                  label="カメラ扱いフォーマット名"
                  hint="入力して Enter で追加します（フォーマット名の完全一致）"
                  persistent-hint
                  multiple
                  chips
                  closable-chips
                  density="comfortable"
                  variant="outlined"
                  :disabled="!isAdmin"
                />
              </template>
            </HelpTip>
          </v-col>
        </v-row>

        <div
          v-if="store.settings"
          class="text-caption text-medium-emphasis mt-4"
        >
          最終更新 {{ formatDateTime(store.settings.updated_at) }}
          / {{ store.settings.updated_by_email || '—' }}
        </div>
      </v-card-text>
      <v-card-actions v-if="isAdmin">
        <v-chip
          v-if="isDirty"
          data-test="dirty-chip"
          color="warning"
          size="small"
          label
        >
          未保存の変更があります
        </v-chip>
        <HelpTip
          v-if="isDirty"
          :text="HELP.settings.discard"
        >
          <template #default="{ props }">
            <v-btn
              v-bind="props"
              data-test="discard-changes"
              variant="text"
              size="small"
              @click="seed(store.settings)"
            >
              変更を破棄
            </v-btn>
          </template>
        </HelpTip>
        <v-spacer />
        <span
          v-if="saveBlockedReason"
          data-test="save-blocked-reason"
          class="text-caption text-warning mr-4"
        >
          {{ saveBlockedReason }}
        </span>
        <!-- disabled なボタンはマウスイベントを受け取らないので、activator をラッパ側に置く -->
        <HelpTip :text="HELP.settings.save">
          <template #default="{ props }">
            <span
              v-bind="props"
              class="d-inline-block"
            >
              <v-btn
                data-test="settings-save"
                color="primary"
                variant="flat"
                :disabled="!canSave"
                @click="save"
              >
                保存
              </v-btn>
            </span>
          </template>
        </HelpTip>
      </v-card-actions>
    </v-card>

    <PriceTableEditor
      class="mb-4"
      :versions="store.prices"
      :editable="isAdmin"
      @add="addPrice"
    />

    <v-dialog
      v-model="leaveDialog"
      max-width="420"
      persistent
    >
      <v-card data-test="leave-dialog">
        <v-card-item>
          <v-card-title>未保存の変更があります</v-card-title>
        </v-card-item>
        <v-card-text class="text-body-2">
          この画面を離れると、保存していない変更は失われます。
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn
            data-test="leave-stay"
            @click="answerLeave(false)"
          >
            このまま編集を続ける
          </v-btn>
          <v-btn
            data-test="leave-discard"
            color="error"
            variant="flat"
            @click="answerLeave(true)"
          >
            破棄して移動する
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>
