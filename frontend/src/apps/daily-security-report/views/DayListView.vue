<script setup lang="ts">
import { onActivated } from 'vue'
import HelpTip from '@/components/HelpTip.vue'
import { formatDateTime } from '@/utils/format'
import LevelChip from '../components/LevelChip.vue'
import { HELP } from '../help'
import { useDaysStore } from '../stores/days'
import type { DaySummary } from '../types'

const store = useDaysStore()
const statuses: Record<DaySummary['status'], { label: string; color: string }> = {
  succeeded: { label: '成功', color: 'success' },
  failed: { label: '失敗', color: 'error' },
  running: { label: '実行中', color: 'info' },
}
onActivated(() => store.fetch())
</script>

<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <HelpTip :text="HELP.days">
        <template #default="{ props }">
          <h1
            v-bind="props"
            class="text-h5"
          >
            レポート一覧
          </h1>
        </template>
      </HelpTip>
      <v-spacer />
      <HelpTip :text="HELP.refresh">
        <template #default="{ props }">
          <v-btn
            v-bind="props"
            data-test="days-refresh"
            prepend-icon="mdi-refresh"
            :loading="store.loading"
            @click="store.fetch()"
          >
            更新
          </v-btn>
        </template>
      </HelpTip>
    </div>
    <p class="text-body-2 text-medium-emphasis mb-4">
      前日 1 日分の集計と通知の履歴です。最新 30 日分を表示します。
    </p>
    <v-alert
      v-if="store.error"
      data-test="days-error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ store.error }}
    </v-alert>
    <v-card
      variant="flat"
      border
    >
      <div class="table-scroll">
        <v-table class="day-table">
          <thead><tr><th>対象日</th><th>段階</th><th>AI の見解</th><th>送信先</th><th>送信時刻（JST）</th><th>詳細</th></tr></thead>
          <tbody>
            <tr
              v-for="(day, index) in store.days"
              :key="day.date ?? index"
              data-test="day-row"
            >
              <td class="text-no-wrap">
                {{ day.date }}
              </td>
              <td>
                <HelpTip :text="HELP.level">
                  <template #default="{ props }">
                    <LevelChip
                      v-bind="props"
                      data-test="day-level"
                      :level="day.status === 'succeeded' ? day.level : null"
                      :legacy="day.legacy"
                    />
                  </template>
                </HelpTip>
                <div class="mt-1">
                  <v-chip
                    :color="Object.hasOwn(statuses, day.status) ? statuses[day.status]?.color : 'grey'"
                    size="small"
                    label
                  >
                    {{ Object.hasOwn(statuses, day.status) ? statuses[day.status]?.label : day.status }}
                  </v-chip>
                </div>
                <div
                  v-if="day.error"
                  class="text-caption text-error"
                >
                  {{ day.error }}
                </div>
              </td>
              <td
                data-test="ai-opinion"
                :class="{ 'text-medium-emphasis': day.ai_status !== 'ok' }"
              >
                {{ [...day.ai_opinion].length > 120 ? [...day.ai_opinion].slice(0, 120).join('') + '…' : day.ai_opinion }}
              </td>
              <td>
                <div
                  v-if="day.notify_channels?.length"
                  class="d-flex flex-wrap ga-1"
                >
                  <v-chip
                    v-for="channel in day.notify_channels"
                    :key="channel"
                    size="small"
                    variant="tonal"
                  >
                    {{ channel === 'email' ? 'メール' : channel === 'slack' ? 'Slack' : channel }}
                  </v-chip>
                </div>
                <span
                  v-else
                  class="text-body-2 text-medium-emphasis"
                >{{ day.notify_note || '—' }}</span>
              </td>
              <td class="text-no-wrap">
                {{ formatDateTime(day.notified_at) }}
              </td>
              <td>
                <v-btn
                  data-test="day-detail"
                  variant="text"
                  :disabled="!day.date"
                  :to="day.date ? { name: 'daily-security-report.day', params: { date: day.date } } : undefined"
                >
                  詳細
                </v-btn>
              </td>
            </tr>
            <tr v-if="!store.days.length && !store.loading">
              <td
                colspan="6"
                class="text-center text-medium-emphasis py-8"
              >
                レポートはまだありません
              </td>
            </tr>
          </tbody>
        </v-table>
      </div>
    </v-card>
  </div>
</template>

<style scoped>
.table-scroll { overflow-x: auto; }
.day-table { min-width: 960px; }
</style>
