<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import EnvBadge from './EnvBadge.vue'
import { useRunsStore } from '../stores/runs'
import type { SettingsView, TargetEnv } from '../types'

const props = defineProps<{ settings: SettingsView | null }>()
const open = defineModel<boolean>({ required: true })

const MONTH_PATTERN = /^\d{4}-(?:0[1-9]|1[0-2])$/
const MAX_MONTHS_BACK = 24
const ENV_LABELS: Record<TargetEnv, string> = { verification: '検証環境', production: '本番' }

const store = useRunsStore()
const targetEnv = ref<TargetEnv>('verification')
const month = ref('')
const distribute = ref(false)
const force = ref(false)
const confirming = ref(false)

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

function monthKey(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}`
}

/**
 * 締め日時を過ぎた最も新しい対象月（設計 2.1 の `target_month` と同じ考え方）。
 * `cutoff_time` の `24:00` は Date の時刻繰り上がりでそのまま翌日 00:00 になる。
 */
function latestClosedMonth(config: SettingsView, now: Date): string {
  const [hour, minute] = config.cutoff_time.split(':').map(Number)
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) {
    return monthKey(now)
  }
  for (let back = 0; back <= MAX_MONTHS_BACK; back += 1) {
    const target = new Date(now.getFullYear(), now.getMonth() - back, 1)
    const cutoff = new Date(
      target.getFullYear(),
      target.getMonth(),
      config.cutoff_day,
      hour,
      minute,
    )
    if (cutoff <= now) {
      return monthKey(target)
    }
  }
  return monthKey(now)
}

function reset(): void {
  confirming.value = false
  distribute.value = false
  force.value = false
  if (!props.settings) {
    return
  }
  targetEnv.value = props.settings.target_env
  month.value = latestClosedMonth(props.settings, new Date())
}

const targets = computed<Record<TargetEnv, boolean>>(
  () => props.settings?.targets ?? { verification: false, production: false },
)

const monthValid = computed(() => MONTH_PATTERN.test(month.value))
const canSubmit = computed(() => monthValid.value && targets.value[targetEnv.value])

const mailTo = computed(() => props.settings?.mail_to ?? [])
const mailCc = computed(() => props.settings?.mail_cc ?? [])
const mailEnabled = computed(() => props.settings?.mail_enabled ?? false)
const slackEnabled = computed(() => props.settings?.slack_enabled ?? false)

/** Slack は配布トグルが OFF か チャンネル ID が空なら投函先から外れる（設計 6.8）。 */
const slackTarget = computed(() => {
  const config = props.settings
  if (!config?.slack_enabled || !config.slack_channel_id) {
    return null
  }
  return config.slack_channel_label || config.slack_channel_id
})

/**
 * 設定で有効になっている配布先。両方 OFF でも配布チェックは操作でき、
 * その場合の実行は送付なしの no-op として成立する。
 */
const distributeTargets = computed(() => {
  const labels = []
  if (mailEnabled.value) {
    labels.push('メール')
  }
  if (slackEnabled.value) {
    labels.push('Slack')
  }
  return labels.length ? `配布先: ${labels.join('・')}` : '配布先: なし（設定で配布が無効）'
})

async function submit(): Promise<void> {
  open.value = false
  await store.createRun({
    month: month.value,
    targetEnv: targetEnv.value,
    distribute: distribute.value,
    force: force.value,
  })
}

watch([open, () => props.settings], ([isOpen]) => {
  if (isOpen) {
    reset()
  }
})

reset()
</script>

<template>
  <v-dialog
    v-model="open"
    max-width="560"
  >
    <v-card data-test="run-dialog">
      <v-card-item>
        <v-card-title>{{ confirming ? '実行の確認' : '手動実行' }}</v-card-title>
      </v-card-item>

      <v-card-text v-if="!settings">
        <v-alert
          type="warning"
          variant="tonal"
          density="compact"
        >
          設定を取得できていないため実行できません。
        </v-alert>
      </v-card-text>

      <v-card-text v-else-if="!confirming">
        <div class="text-body-2 text-medium-emphasis mb-1">
          集計対象環境
        </div>
        <v-btn-toggle
          v-model="targetEnv"
          data-test="run-env"
          mandatory
          density="comfortable"
          variant="outlined"
          divided
          class="mb-1"
        >
          <v-btn
            value="verification"
            data-test="run-env-verification"
            :disabled="!targets.verification"
          >
            検証環境
          </v-btn>
          <v-btn
            value="production"
            data-test="run-env-production"
            :disabled="!targets.production"
          >
            本番
          </v-btn>
        </v-btn-toggle>
        <div class="text-caption text-medium-emphasis mb-4">
          接続情報が未登録の環境は選べません。
        </div>

        <v-text-field
          v-model="month"
          data-test="run-month"
          label="対象月"
          placeholder="YYYY-MM"
          density="comfortable"
          variant="outlined"
          :error-messages="monthValid ? [] : ['対象月は YYYY-MM 形式で指定してください']"
        />

        <v-checkbox
          v-model="distribute"
          data-test="run-distribute"
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
          v-model="force"
          data-test="run-force"
          label="強制再実行（成功済みの月を集計し直す）"
          density="compact"
          hide-details
        />
      </v-card-text>

      <v-card-text
        v-else
        data-test="run-confirm"
      >
        <div class="d-flex align-center ga-2 mb-3">
          <EnvBadge :target-env="targetEnv" />
          <span class="text-body-2">{{ settings.saasa_host ?? '接続先ホスト不明' }}</span>
        </div>
        <p class="text-body-2 mb-3">
          {{ ENV_LABELS[targetEnv] }}の {{ month }} を集計します。
        </p>
        <template v-if="distribute">
          <div class="text-body-2 font-weight-medium mb-1">
            配布先
          </div>
          <ul class="text-body-2 ps-5 mb-0">
            <li v-if="mailEnabled">
              メール To: {{ mailTo.join(', ') || '未設定' }}
            </li>
            <li v-if="mailEnabled && mailCc.length">
              メール Cc: {{ mailCc.join(', ') }}
            </li>
            <li v-if="slackTarget">
              Slack: {{ slackTarget }}
              <span v-if="settings.slack_mode === 'webhook'">
                （Webhook フォールバック中のためチャンネル ID は無視されます）
              </span>
            </li>
            <li v-if="!mailEnabled && !slackTarget">
              設定で配布が無効のため送付しません
            </li>
          </ul>
        </template>
        <p
          v-else
          class="text-body-2 mb-0"
        >
          配布しません（画面と Excel の確認のみ）。
        </p>
      </v-card-text>

      <v-card-text
        v-if="force && settings"
        class="pt-0"
      >
        <v-alert
          data-test="force-warning"
          type="warning"
          variant="tonal"
          density="compact"
        >
          強制再実行は二重起動の防止を外します。実行中の月に使うと二重実行になることがあります。
        </v-alert>
      </v-card-text>

      <v-card-actions>
        <v-spacer />
        <v-btn
          v-if="confirming"
          data-test="run-back"
          @click="confirming = false"
        >
          戻る
        </v-btn>
        <v-btn
          v-else
          data-test="run-cancel"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          v-if="confirming"
          data-test="run-submit"
          color="primary"
          variant="flat"
          @click="submit"
        >
          実行
        </v-btn>
        <v-btn
          v-else
          data-test="run-next"
          color="primary"
          variant="flat"
          :disabled="!canSubmit"
          @click="confirming = true"
        >
          確認へ
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
