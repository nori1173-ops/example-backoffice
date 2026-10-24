<script setup lang="ts">
import { computed, onActivated, onDeactivated, ref } from 'vue'
import HelpTip from '@/components/HelpTip.vue'
import { useUiStore } from '@/stores/ui'
import LevelChip from './LevelChip.vue'
import { jstToday, RUN_MAX_AGE_DAYS, shiftDate, useRunNow } from '../composables/useRunNow'
import { HELP } from '../help'

const props = defineProps<{ disabled: boolean; dirty: boolean }>()
const ui = useUiStore()
const run = useRunNow()
const today = ref(jstToday())
const targetDate = ref(shiftDate(today.value, -1))
const confirmOpen = ref(false)
const minDate = computed(() => shiftDate(today.value, -RUN_MAX_AGE_DAYS))
const isToday = computed(() => targetDate.value === today.value)
const dateError = computed(() => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(targetDate.value)) return '対象日を選んでください'
  if (targetDate.value > today.value) return '未来の日は選べません'
  if (targetDate.value < minDate.value) return `${RUN_MAX_AGE_DAYS} 日より前の日は選べません`
  return ''
})
const canRun = computed(() => !props.disabled && !dateError.value && !run.busy.value)
const opinion = computed(() => {
  const chars = [...(run.result.value?.ai_opinion || run.result.value?.ai?.opinion || '')]
  return chars.length > 120 ? `${chars.slice(0, 120).join('')}…` : chars.join('')
})
function openConfirm(): void {
  if (canRun.value) confirmOpen.value = true
}
async function confirm(): Promise<void> {
  confirmOpen.value = false
  if (!canRun.value) return
  if (await run.start(targetDate.value)) ui.notify('受け付けました。1〜3 分で届きます', 'success')
}
onActivated(() => { today.value = jstToday() })
onDeactivated(run.stop)
</script>

<template>
  <v-card
    variant="flat"
    border
    class="mb-4"
    data-test="run-now"
  >
    <v-card-text>
      <HelpTip :text="HELP.runNow">
        <template #default="{ props: tip }">
          <h2
            v-bind="tip"
            class="text-h6 mb-3"
          >
            今すぐ実行
          </h2>
        </template>
      </HelpTip>
      <p class="text-body-2 mb-3">
        07:00 の自動実行と同じ処理で選んだ日のレポートを作り、設定の宛先へ送ります。送信済みの日も集計し直して再送します。
      </p>
      <v-alert
        v-if="dirty"
        data-test="run-now-dirty"
        type="warning"
        variant="tonal"
        density="compact"
        class="mb-3"
      >
        未保存の変更は反映されません（保存してから実行してください）
      </v-alert>
      <div class="d-flex align-start flex-wrap ga-3">
        <v-text-field
          v-model="targetDate"
          data-test="run-now-date"
          type="date"
          label="対象日"
          :min="minDate"
          :max="today"
          :disabled="disabled || run.busy.value"
          :error-messages="disabled ? '' : dateError"
          :hint="isToday ? '当日は途中までの集計になります' : ''"
          persistent-hint
          variant="outlined"
          style="max-width: 300px"
        />
        <v-btn
          data-test="run-now-start"
          color="primary"
          variant="tonal"
          :disabled="!canRun"
          :loading="run.busy.value"
          @click="openConfirm"
        >
          今すぐ実行
        </v-btn>
      </div>
      <div
        v-if="run.busy.value || run.error.value || run.result.value"
        data-test="run-now-panel"
        class="mt-3"
        role="status"
        aria-live="polite"
      >
        <p v-if="run.busy.value">
          {{ run.date.value }} 分を実行中です。5 秒ごとに結果を確認します（最大 15 分）。
        </p>
        <v-alert
          v-if="run.error.value"
          type="error"
          variant="tonal"
          class="mb-3"
        >
          {{ run.error.value }}
        </v-alert>
        <div
          v-if="run.result.value"
          data-test="run-now-result"
        >
          <p class="text-subtitle-1">
            {{ run.date.value }} 分: {{ run.result.value.status === 'succeeded' ? '成功' : '失敗' }}
            <LevelChip
              :level="run.result.value.level"
              :legacy="run.result.value.legacy"
            />
          </p>
          <p class="text-subtitle-2 mt-2">
            AI の見解
          </p>
          <p class="run-opinion">
            {{ opinion || '—' }}
          </p>
          <router-link :to="{ name: 'daily-security-report.day', params: { date: run.date.value } }">
            詳細を開く
          </router-link>
        </div>
      </div>
    </v-card-text>
  </v-card>
  <v-dialog
    v-model="confirmOpen"
    max-width="480"
  >
    <v-card data-test="run-now-dialog">
      <v-card-title>レポートを今すぐ実行</v-card-title>
      <v-card-text>
        <p>{{ targetDate }} 分を実行し、設定の宛先（Slack / メール）に送ります。よろしいですか？</p>
        <p
          v-if="isToday"
          class="mt-2"
        >
          当日は途中までの集計になります。
        </p>
        <p
          v-if="dirty"
          class="mt-2"
        >
          未保存の変更は反映されません（保存してから実行してください）。
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="run-now-cancel"
          @click="confirmOpen = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="run-now-confirm"
          color="primary"
          variant="flat"
          @click="confirm"
        >
          実行する
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.run-opinion { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
