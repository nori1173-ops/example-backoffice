<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'
import EnvBadge from '../components/EnvBadge.vue'
import PriceTableEditor from '../components/PriceTableEditor.vue'
import { useSettingsStore } from '../stores/settings'
import type { AuditEntry, PriceTable, SettingsView, TargetEnv } from '../types'

interface SettingsForm {
  target_env: TargetEnv
  cutoff_day: string
  cutoff_time: string
  auto_retry_days: string
  auto_run_enabled: boolean
  auto_run_from_month: string
  mail_enabled: boolean
  slack_enabled: boolean
  mail_to: string[]
  mail_cc: string[]
  slack_channel_id: string
  slack_channel_label: string
  slack_mention: string
  slack_template: string
  exclude_keywords: string[]
  camera_format_names: string[]
}

const CUTOFF_DAY_MIN = 1
const CUTOFF_DAY_MAX = 28
const AUTO_RETRY_DAYS_MIN = 1
const AUTO_RETRY_DAYS_MAX = 31
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
const AUDIT_TARGET_LABELS: Record<AuditEntry['target'], string> = {
  settings: '設定',
  price: '単価表',
}

const auth = useAuthStore()
const store = useSettingsStore()
const isAdmin = computed(() => auth.groups.includes('admin'))
const saved = ref(false)

const form = reactive<SettingsForm>({
  target_env: 'verification',
  cutoff_day: '',
  cutoff_time: '',
  auto_retry_days: '',
  auto_run_enabled: false,
  auto_run_from_month: '',
  mail_enabled: false,
  slack_enabled: false,
  mail_to: [],
  mail_cc: [],
  slack_channel_id: '',
  slack_channel_label: '',
  slack_mention: '',
  slack_template: '',
  exclude_keywords: [],
  camera_format_names: [],
})

function seed(settings: SettingsView | null): void {
  if (!settings) {
    return
  }
  form.target_env = settings.target_env
  form.cutoff_day = String(settings.cutoff_day)
  form.cutoff_time = settings.cutoff_time
  form.auto_retry_days = String(settings.auto_retry_days)
  form.auto_run_enabled = settings.auto_run_enabled
  form.auto_run_from_month = settings.auto_run_from_month
  form.mail_enabled = settings.mail_enabled
  form.slack_enabled = settings.slack_enabled
  form.mail_to = [...settings.mail_to]
  form.mail_cc = [...settings.mail_cc]
  form.slack_channel_id = settings.slack_channel_id
  form.slack_channel_label = settings.slack_channel_label
  form.slack_mention = settings.slack_mention
  form.slack_template = settings.slack_template
  form.exclude_keywords = [...settings.exclude_keywords]
  form.camera_format_names = [...settings.camera_format_names]
}

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
    : ['チャンネル ID は C または G で始まる英数字です（#名前 は不可）']
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
    auto_retry_days: inRange(form.auto_retry_days, AUTO_RETRY_DAYS_MIN, AUTO_RETRY_DAYS_MAX)
      ? []
      : [`自動再実行上限日数は ${AUTO_RETRY_DAYS_MIN}〜${AUTO_RETRY_DAYS_MAX} で指定してください`],
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
const canSave = computed(
  () => isAdmin.value && !hasError.value && store.targets[form.target_env] && !store.loading,
)

const unregisteredEnvLabels = computed(() =>
  (Object.keys(TARGET_ENV_LABELS) as TargetEnv[])
    .filter((env) => !store.targets[env])
    .map((env) => TARGET_ENV_LABELS[env]),
)

const saveBlockedReason = computed(() => {
  if (!store.targets[form.target_env]) {
    return `${TARGET_ENV_LABELS[form.target_env]}の接続情報（SSM）が未登録のため保存できません`
  }
  if (hasError.value) {
    return '入力エラーがあるため保存できません'
  }
  return ''
})

function formatDateTime(iso: string): string {
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso)
  return parts ? `${parts[1]}/${parts[2]}/${parts[3]} ${parts[4]}:${parts[5]}` : iso
}

function changedKeys(entry: AuditEntry): string {
  const keys = Object.keys(entry.after).filter(
    (key) => JSON.stringify(entry.before[key]) !== JSON.stringify(entry.after[key]),
  )
  return keys.join(', ') || '—'
}

async function save(): Promise<void> {
  saved.value = false
  saved.value = await store.save({
    ...form,
    cutoff_day: Number(form.cutoff_day),
    auto_retry_days: Number(form.auto_retry_days),
  })
}

async function addPrice(price: PriceTable): Promise<void> {
  await store.addPrice(price)
}

watch(() => store.settings, seed, { immediate: true })

onMounted(async () => {
  await store.fetch()
  if (isAdmin.value) {
    await store.fetchAudits()
  }
})
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
      <v-card-text
        data-test="target-summary"
        class="d-flex align-center flex-wrap ga-4"
      >
        <div class="d-flex align-center ga-2">
          <span class="text-body-2 text-medium-emphasis">集計対象環境</span>
          <EnvBadge
            v-if="store.settings"
            :target-env="store.settings.target_env"
          />
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

    <v-alert
      v-if="saved"
      type="success"
      variant="tonal"
      class="mb-4"
    >
      設定を保存しました。
    </v-alert>

    <v-card
      variant="flat"
      border
      class="mb-4"
    >
      <v-card-text>
        <div class="text-body-2 text-medium-emphasis mb-1">
          集計対象環境
        </div>
        <v-btn-toggle
          v-model="form.target_env"
          data-test="target-env"
          mandatory
          density="comfortable"
          variant="outlined"
          divided
          :disabled="!isAdmin"
          class="mb-1"
        >
          <v-btn
            value="verification"
            data-test="target-env-verification"
            :disabled="!store.targets.verification"
          >
            検証環境
          </v-btn>
          <v-btn
            value="production"
            data-test="target-env-production"
            :disabled="!store.targets.production"
          >
            本番
          </v-btn>
        </v-btn-toggle>
        <div
          v-if="unregisteredEnvLabels.length"
          data-test="target-env-hint"
          class="text-caption text-warning mb-4"
        >
          {{ unregisteredEnvLabels.join('・') }}は接続情報（SSM）が未登録のため選べません。
          管理者が SSM に接続情報を登録すると選択できます。
        </div>

        <v-row dense>
          <v-col
            cols="12"
            sm="4"
          >
            <v-text-field
              v-model="form.cutoff_day"
              data-test="cutoff-day"
              label="締め日（毎月）"
              density="comfortable"
              variant="outlined"
              :readonly="!isAdmin"
              :error-messages="errors.cutoff_day"
            />
          </v-col>
          <v-col
            cols="12"
            sm="4"
          >
            <v-text-field
              v-model="form.cutoff_time"
              data-test="cutoff-time"
              label="締め時刻"
              hint="24:00 はその日の終わりまで含む"
              persistent-hint
              density="comfortable"
              variant="outlined"
              :readonly="!isAdmin"
              :error-messages="errors.cutoff_time"
            />
          </v-col>
          <v-col
            cols="12"
            sm="4"
          >
            <v-text-field
              v-model="form.auto_retry_days"
              data-test="auto-retry-days"
              label="自動再実行上限日数"
              density="comfortable"
              variant="outlined"
              :readonly="!isAdmin"
              :error-messages="errors.auto_retry_days"
            />
          </v-col>
          <v-col
            cols="12"
            sm="4"
          >
            <v-text-field
              v-model="form.auto_run_from_month"
              data-test="auto-run-from-month"
              label="自動実行の開始月"
              placeholder="YYYY-MM"
              density="comfortable"
              variant="outlined"
              :readonly="!isAdmin"
              :error-messages="errors.auto_run_from_month"
            />
          </v-col>
          <v-col
            cols="12"
            sm="4"
            class="d-flex align-center"
          >
            <v-switch
              v-model="form.auto_run_enabled"
              data-test="auto-run-enabled"
              label="自動実行を有効にする"
              color="primary"
              density="comfortable"
              hide-details
              :disabled="!isAdmin"
            />
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
            <v-switch
              v-model="form.mail_enabled"
              data-test="mail-enabled"
              label="メール配布を有効にする"
              hint="OFF のときは宛先を空にできます"
              persistent-hint
              color="primary"
              density="comfortable"
              :disabled="!isAdmin"
            />
          </v-col>
          <v-col
            cols="12"
            sm="6"
          >
            <v-combobox
              v-model="form.mail_to"
              data-test="mail-to"
              label="メール宛先 To"
              multiple
              chips
              closable-chips
              density="comfortable"
              variant="outlined"
              :readonly="!isAdmin"
              :error-messages="errors.mail_to"
            />
          </v-col>
          <v-col
            cols="12"
            sm="6"
          >
            <v-combobox
              v-model="form.mail_cc"
              data-test="mail-cc"
              label="メール宛先 Cc"
              multiple
              chips
              closable-chips
              density="comfortable"
              variant="outlined"
              :readonly="!isAdmin"
              :error-messages="errors.mail_cc"
            />
          </v-col>
          <v-col cols="12">
            <v-switch
              v-model="form.slack_enabled"
              data-test="slack-enabled"
              label="Slack 配布を有効にする"
              hint="OFF のときはチャンネル ID を空にできます"
              persistent-hint
              color="primary"
              density="comfortable"
              :disabled="!isAdmin"
            />
          </v-col>
          <v-col
            cols="12"
            sm="4"
          >
            <v-text-field
              v-model="form.slack_channel_id"
              data-test="slack-channel-id"
              label="Slack チャンネル ID"
              placeholder="C0123ABCD"
              density="comfortable"
              variant="outlined"
              :readonly="!isAdmin"
              :error-messages="errors.slack_channel_id"
            />
          </v-col>
          <v-col
            cols="12"
            sm="4"
          >
            <v-text-field
              v-model="form.slack_channel_label"
              data-test="slack-channel-label"
              label="Slack チャンネル表示名"
              hint="画面表示用のメモ。投稿先の決定には使わない"
              persistent-hint
              density="comfortable"
              variant="outlined"
              :readonly="!isAdmin"
            />
          </v-col>
          <v-col
            cols="12"
            sm="4"
          >
            <v-text-field
              v-model="form.slack_mention"
              data-test="slack-mention"
              label="Slack メンション"
              placeholder="<!here>"
              density="comfortable"
              variant="outlined"
              :readonly="!isAdmin"
            />
          </v-col>
          <v-col cols="12">
            <v-textarea
              v-model="form.slack_template"
              data-test="slack-template"
              label="Slack 文面"
              rows="3"
              auto-grow
              density="comfortable"
              variant="outlined"
              :readonly="!isAdmin"
              :error-messages="errors.slack_template"
              :hint="`使えるプレースホルダ: ${ALLOWED_PLACEHOLDERS.join(' ')}`"
              persistent-hint
            />
          </v-col>
          <v-col
            cols="12"
            sm="6"
          >
            <v-combobox
              v-model="form.exclude_keywords"
              data-test="exclude-keywords"
              label="除外キーワード"
              hint="テナント説明欄・地点備考欄に含まれたら課金対象外"
              persistent-hint
              multiple
              chips
              closable-chips
              density="comfortable"
              variant="outlined"
              :readonly="!isAdmin"
            />
          </v-col>
          <v-col
            cols="12"
            sm="6"
          >
            <v-combobox
              v-model="form.camera_format_names"
              data-test="camera-format-names"
              label="カメラ扱いフォーマット名"
              hint="フォーマット名の完全一致。受信が無いカメラ地点の補強用"
              persistent-hint
              multiple
              chips
              closable-chips
              density="comfortable"
              variant="outlined"
              :readonly="!isAdmin"
            />
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
        <v-spacer />
        <span
          v-if="saveBlockedReason"
          data-test="save-blocked-reason"
          class="text-caption text-warning mr-4"
        >
          {{ saveBlockedReason }}
        </span>
        <v-btn
          data-test="settings-save"
          color="primary"
          variant="flat"
          :disabled="!canSave"
          @click="save"
        >
          保存
        </v-btn>
      </v-card-actions>
    </v-card>

    <PriceTableEditor
      class="mb-4"
      :versions="store.prices"
      :editable="isAdmin"
      @add="addPrice"
    />

    <v-card
      v-if="isAdmin"
      data-test="audit-block"
      variant="flat"
      border
    >
      <v-card-item>
        <v-card-title class="text-subtitle-1">
          変更履歴
        </v-card-title>
        <v-card-subtitle>直近 50 件を新しい順に表示します。</v-card-subtitle>
      </v-card-item>
      <v-table density="comfortable">
        <thead>
          <tr>
            <th>日時</th>
            <th>対象</th>
            <th>実行者</th>
            <th>変更項目</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!store.audits.length">
            <td
              colspan="4"
              class="text-center text-medium-emphasis py-6"
            >
              変更履歴がありません。
            </td>
          </tr>
          <tr
            v-for="entry in store.audits"
            :key="`${entry.at}-${entry.target}`"
            data-test="audit-row"
          >
            <td class="text-no-wrap">
              {{ formatDateTime(entry.at) }}
            </td>
            <td>{{ AUDIT_TARGET_LABELS[entry.target] }}</td>
            <td>{{ entry.actor_email }}</td>
            <td>{{ changedKeys(entry) }}</td>
          </tr>
        </tbody>
      </v-table>
    </v-card>
  </div>
</template>
