import { onScopeDispose, ref } from 'vue'
import { extractApiError } from '@/services/http'
import { reportApi } from '../api'
import type { PreviewRequest, PreviewResult } from '../types'

export function useAiPreview() {
  const busy = ref(false)
  const result = ref<PreviewResult | null>(null)
  const error = ref<string | null>(null)
  const requested = ref<PreviewRequest | null>(null)
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
  async function start(body: PreviewRequest): Promise<void> {
    if (busy.value) return
    stop()
    const current = generation
    busy.value = true
    result.value = null
    error.value = null
    requested.value = { ...body }
    try {
      const accepted = await reportApi.startPreview(body)
      if (current !== generation) return
      if (!accepted?.preview_id) throw new Error('empty preview')
      deadlineTimer = setTimeout(() => fail('5 分以内に試行が完了しませんでした。時間を置いてもう一度試してください。'), 300000)
      const poll = async (): Promise<void> => {
        try {
          const response = await reportApi.getPreview(accepted.preview_id)
          if (current !== generation) return
          if (!response || (response.status === 'succeeded' && !response.result)) throw new Error('empty result')
          if (response.status === 'running') {
            pollTimer = setTimeout(() => { void poll() }, 2000)
            return
          }
          result.value = response
          if (response.status === 'failed') error.value = response.error?.message ?? 'AI の試行に失敗しました'
          stop()
        } catch (e: unknown) {
          if (current === generation) fail(extractApiError(e)?.message ?? '試行結果の取得に失敗しました')
        }
      }
      pollTimer = setTimeout(() => { void poll() }, 2000)
    } catch (e: unknown) {
      if (current === generation) fail(extractApiError(e)?.message ?? 'AI の試行を開始できませんでした')
    }
  }
  onScopeDispose(stop)
  return { busy, result, error, requested, start, stop }
}
