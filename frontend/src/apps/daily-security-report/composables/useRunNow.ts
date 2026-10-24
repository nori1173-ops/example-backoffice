import { onScopeDispose, ref } from 'vue'
import { extractApiError } from '@/services/http'
import { reportApi } from '../api'
import type { DayDetail } from '../types'

export const RUN_MAX_AGE_DAYS = 400
const POLL_MS = 5000
const DEADLINE_MS = 15 * 60 * 1000

export function jstToday(): string {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Tokyo' }).format(new Date())
}

export function shiftDate(date: string, days: number): string {
  const shifted = new Date(`${date}T00:00:00Z`)
  shifted.setUTCDate(shifted.getUTCDate() + days)
  return shifted.toISOString().slice(0, 10)
}

const httpStatus = (e: unknown) => (e as { response?: { status?: number } })?.response?.status

function finishedSince(detail: DayDetail | null, since: number): detail is DayDetail {
  return !!detail && (detail.status === 'succeeded' || detail.status === 'failed')
    && !!detail.started_at && Date.parse(detail.started_at) >= since
}

export function useRunNow() {
  const busy = ref(false)
  const date = ref<string | null>(null)
  const result = ref<DayDetail | null>(null)
  const error = ref<string | null>(null)
  let generation = 0
  let pollTimer: ReturnType<typeof setTimeout> | undefined
  let deadlineTimer: ReturnType<typeof setTimeout> | undefined
  function stop(): void {
    generation++
    clearTimeout(pollTimer)
    clearTimeout(deadlineTimer)
    busy.value = false
  }
  function fail(message: string): void {
    error.value = message
    stop()
  }
  async function start(target: string): Promise<boolean> {
    if (busy.value) return false
    stop()
    const current = generation
    busy.value = true
    date.value = target
    result.value = null
    error.value = null
    try {
      const accepted = await reportApi.runDay(target)
      if (current !== generation) return false
      if (!accepted?.accepted_at) throw new Error('empty run')
      const since = Date.parse(accepted.accepted_at)
      deadlineTimer = setTimeout(() => fail('15 分以内に完了を確認できませんでした。レポート一覧で状況を確認してください。'), DEADLINE_MS)
      const poll = async (): Promise<void> => {
        try {
          const detail = await reportApi.getDay(target)
          if (current !== generation) return
          if (finishedSince(detail, since)) {
            result.value = detail
            if (detail.status === 'failed') error.value = `実行に失敗しました（${detail.error || '理由不明'}）`
            stop()
            return
          }
        } catch (e: unknown) {
          if (current !== generation) return
          if (httpStatus(e) !== 404 && extractApiError(e)?.code !== 'NOT_FOUND') {
            fail(extractApiError(e)?.message ?? '実行結果の取得に失敗しました。レポート一覧で確認してください。')
            return
          }
        }
        pollTimer = setTimeout(() => { void poll() }, POLL_MS)
      }
      pollTimer = setTimeout(() => { void poll() }, POLL_MS)
      return true
    } catch (e: unknown) {
      if (current !== generation) return false
      const apiError = extractApiError(e)
      fail(apiError?.code === 'RUN_IN_PROGRESS' || httpStatus(e) === 409 ? '同じ日を実行中です'
        : apiError?.message ?? '実行を開始できませんでした')
      return false
    }
  }
  onScopeDispose(stop)
  return { busy, date, result, error, start, stop }
}
