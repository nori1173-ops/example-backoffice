import { onScopeDispose, ref } from 'vue'
import { extractApiError } from '@/services/http'
import { reportApi } from '../api'
import { logErrorMessage } from '../utils/logs'
import type { LogQueryError, LogQueryResponse, LogQueryResult, LogRef } from '../types'

export type LogQueryState = 'idle' | 'running' | 'succeeded' | 'failed'
const POLL_MS = 2000
const DEADLINE_MS = 90000

const httpStatus = (e: unknown) => (e as { response?: { status?: number } })?.response?.status

function errorParts(error: LogQueryError | null | undefined): { code: string | null; message: string | null } {
  if (!error) return { code: null, message: null }
  if (typeof error === 'string') return { code: error, message: null }
  return { code: error.code || null, message: error.message || null }
}

export function useLogQuery() {
  const status = ref<LogQueryState>('idle')
  const result = ref<LogQueryResult | null>(null)
  const error = ref<string | null>(null)
  const errorCode = ref<string | null>(null)
  let generation = 0
  let pollTimer: ReturnType<typeof setTimeout> | undefined
  let deadlineTimer: ReturnType<typeof setTimeout> | undefined
  function halt(): void {
    generation++
    clearTimeout(pollTimer)
    clearTimeout(deadlineTimer)
  }
  function fail(code: string | null, message: string): void {
    halt()
    status.value = 'failed'
    errorCode.value = code
    error.value = message
  }
  function settle(response: LogQueryResponse): boolean {
    if (response.status === 'running') return false
    if (response.status === 'succeeded' && response.result) {
      halt()
      result.value = response.result
      status.value = 'succeeded'
      return true
    }
    const { code, message } = errorParts(response.error)
    fail(code, response.status === 'succeeded' ? '結果が空でした' : logErrorMessage(code, message))
    return true
  }
  function close(): void {
    halt()
    status.value = 'idle'
    result.value = null
    error.value = null
    errorCode.value = null
  }
  async function open(date: string, target: LogRef): Promise<void> {
    close()
    const current = generation
    status.value = 'running'
    try {
      const accepted = await reportApi.startLogs(date, target)
      if (current !== generation) return
      if (!accepted?.status) throw new Error('empty logs')
      if (settle(accepted)) return
      const queryId = accepted.query_id
      if (!queryId) throw new Error('empty query id')
      deadlineTimer = setTimeout(() => fail('TIMEOUT', '90 秒待っても結果が返りませんでした'), DEADLINE_MS)
      const poll = async (): Promise<void> => {
        try {
          const response = await reportApi.getLogs(date, queryId)
          if (current !== generation) return
          if (!response?.status) throw new Error('empty result')
          if (!settle(response)) pollTimer = setTimeout(() => { void poll() }, POLL_MS)
        } catch (e: unknown) {
          if (current !== generation) return
          const apiError = extractApiError(e)
          fail(apiError?.code ?? null, apiError ? logErrorMessage(apiError.code, apiError.message) : '結果の取得に失敗しました')
        }
      }
      pollTimer = setTimeout(() => { void poll() }, POLL_MS)
    } catch (e: unknown) {
      if (current !== generation) return
      const apiError = extractApiError(e)
      const code = httpStatus(e) === 429 ? 'TOO_MANY_QUERIES' : apiError?.code ?? null
      fail(code, (code || apiError) ? logErrorMessage(code, apiError?.message) : '問い合わせを開始できませんでした')
    }
  }
  onScopeDispose(halt)
  return { status, result, error, errorCode, open, close }
}
