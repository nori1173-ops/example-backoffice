<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useUiStore } from '@/stores/ui'
import HelpTip from '@/components/HelpTip.vue'
import RunConfirmSummary from './RunConfirmSummary.vue'
import { HELP } from '../help'
import { useRunsStore } from '../stores/runs'
import type { SettingsView, TargetEnv } from '../types'

const props = defineProps<{ settings: SettingsView | null }>()
const open = defineModel<boolean>({ required: true })

const MONTH_PATTERN = /^\d{4}-(?:0[1-9]|1[0-2])$/
const MAX_MONTHS_BACK = 24
const ENV_LABELS: Record<TargetEnv, string> = { verification: '検証環境', production: '本番' }
// 集計対象環境はサイトごとに固定（設計 R1 / UI-08）。取り違えないよう文言と色で明示する
const ENV_HEADLINES: Record<TargetEnv, string> = {
  verification: '検証環境の SaaS_A を集計',
  production: '本番 SaaS_A を集計（実データ）',
}
const ENV_COLORS: Record<TargetEnv, string> = { verification: 'orange', production: 'green' }

const store = useRunsStore()
const ui = useUiStore()
const month = ref('')
const distribute = ref(false)
const force = ref(false)
const confirming = ref(false)
const distributeAck = ref(false)

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
  distributeAck.value = false
  if (!props.settings) {
    return
  }
  month.value = latestClosedMonth(props.settings, new Date())
}

// 固定環境のキーだけが届く（設計 R1）
const targets = computed<Partial<Record<TargetEnv, boolean>>>(() => props.settings?.targets ?? {})

const fixedEnv = computed<TargetEnv | null>(() => props.settings?.target_env ?? null)
const targetReady = computed(() => !!fixedEnv.value && !!targets.value[fixedEnv.value])

const targetHint = computed(() =>
  fixedEnv.value && !targetReady.value
    ? `${ENV_LABELS[fixedEnv.value]}の接続情報（SSM）が未登録のため実行できません`
    : '',
)

const monthValid = computed(() => MONTH_PATTERN.test(month.value))
const canSubmit = computed(() => monthValid.value && targetReady.value)

const mailEnabled = computed(() => props.settings?.mail_enabled ?? false)
const slackEnabled = computed(() => props.settings?.slack_enabled ?? false)

// 配布は実際の宛先を読んだ確認を挟む（設計 R6 / UI-07）
const confirmed = computed(() => !distribute.value || distributeAck.value)
const dangerous = computed(() => props.settings?.target_env === 'production' && distribute.value)

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
  const accepted = await store.createRun({
    month: month.value,
    distribute: distribute.value,
    force: force.value,
  })
  if (accepted) {
    ui.notify(`${month.value} の実行を受け付けました`, 'info')
  } else {
    ui.notify(store.error ?? '実行の開始に失敗しました', 'error')
  }
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
        <div class="mb-4">
          <div class="text-body-2 text-medium-emphasis mb-2">
            集計対象環境
          </div>
          <v-chip
            data-test="run-fixed-env"
            :color="ENV_COLORS[settings.target_env]"
            variant="flat"
            size="large"
            label
          >
            {{ ENV_HEADLINES[settings.target_env] }}
          </v-chip>
          <div class="text-caption text-medium-emphasis mt-1">
            このサイトでは固定です（接続先: {{ settings.saasa_host ?? '未登録' }}）。
          </div>
          <div
            v-if="targetHint"
            data-test="run-target-hint"
            class="text-caption text-warning mt-1"
          >
            {{ targetHint }}
          </div>
        </div>

        <HelpTip :text="HELP.runDialog.month">
          <template #default="{ props: tip }">
            <v-text-field
              v-bind="tip"
              v-model="month"
              data-test="run-month"
              label="対象月"
              placeholder="YYYY-MM"
              density="comfortable"
              variant="outlined"
              :error-messages="monthValid ? [] : ['対象月は YYYY-MM 形式で指定してください']"
            />
          </template>
        </HelpTip>

        <HelpTip :text="HELP.runDialog.distribute">
          <template #default="{ props: tip }">
            <v-checkbox
              v-bind="tip"
              v-model="distribute"
              data-test="run-distribute"
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
          <template #default="{ props: tip }">
            <v-checkbox
              v-bind="tip"
              v-model="force"
              data-test="run-force"
              label="強制再実行（成功済みの月を集計し直す）"
              density="compact"
              hide-details
            />
          </template>
        </HelpTip>
      </v-card-text>

      <v-card-text
        v-else
        data-test="run-confirm"
      >
        <RunConfirmSummary
          :settings="settings"
          :month="month"
          :distribute="distribute"
        />
        <HelpTip
          v-if="distribute"
          :text="HELP.runDialog.ack"
        >
          <template #default="{ props: tip }">
            <v-checkbox
              v-bind="tip"
              v-model="distributeAck"
              data-test="distribute-ack"
              label="上の配布先へ送ることを確認しました"
              density="compact"
              hide-details
              class="mt-2"
            />
          </template>
        </HelpTip>
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
        <HelpTip :text="confirming ? HELP.runDialog.back : HELP.runDialog.cancel">
          <template #default="{ props: tip }">
            <v-btn
              v-if="confirming"
              v-bind="tip"
              data-test="run-back"
              @click="confirming = false"
            >
              戻る
            </v-btn>
            <v-btn
              v-else
              v-bind="tip"
              data-test="run-cancel"
              @click="open = false"
            >
              キャンセル
            </v-btn>
          </template>
        </HelpTip>
        <!-- disabled なボタンはマウスイベントを受け取らないので、activator をラッパ側に置く -->
        <HelpTip :text="confirming ? HELP.runDialog.submit : HELP.runDialog.next">
          <template #default="{ props: tip }">
            <span
              v-bind="tip"
              class="d-inline-block"
            >
              <v-btn
                v-if="confirming"
                data-test="run-submit"
                :color="dangerous ? 'error' : 'primary'"
                variant="flat"
                :disabled="!confirmed"
                @click="submit"
              >
                {{ dangerous ? '本番へ配布して実行' : '実行' }}
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
            </span>
          </template>
        </HelpTip>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
