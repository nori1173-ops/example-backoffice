<script setup lang="ts">
import { computed } from 'vue'
import EnvBadge from './EnvBadge.vue'
import type { SettingsView, TargetEnv } from '../types'

const props = defineProps<{
  settings: SettingsView | null
  month: string
  distribute: boolean
}>()

const ENV_LABELS: Record<TargetEnv, string> = { verification: '検証環境', production: '本番' }

const mailEnabled = computed(() => props.settings?.mail_enabled ?? false)
const mailTo = computed(() => props.settings?.mail_to ?? [])
const mailCc = computed(() => props.settings?.mail_cc ?? [])

/** Slack は配布トグルが OFF か チャンネル ID が空なら投函先から外れる（設計 6.8）。 */
const slackTarget = computed(() => {
  const config = props.settings
  if (!config?.slack_enabled || !config.slack_channel_id) {
    return null
  }
  return config.slack_channel_label || config.slack_channel_id
})
</script>

<template>
  <div
    v-if="settings"
    data-test="run-confirm-summary"
  >
    <div class="d-flex align-center ga-2 mb-3">
      <EnvBadge :target-env="settings.target_env" />
      <span class="text-body-2">{{ settings.saasa_host ?? '接続先ホスト不明' }}</span>
    </div>
    <p class="text-body-2 mb-3">
      {{ ENV_LABELS[settings.target_env] }}の {{ month }} を集計します。
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
  </div>
</template>
