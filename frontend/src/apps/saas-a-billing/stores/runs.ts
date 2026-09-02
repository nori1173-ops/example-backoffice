import { defineStore } from 'pinia'
import { ref } from 'vue'
import { extractApiError } from '@/services/http'
import { billingApi } from '../api'
import type { RunAccepted, RunRequest, RunStatus, RunSummary, TargetEnv } from '../types'

const POLL_INTERVAL_MS = 5_000
const POLL_TIMEOUT_MS = 10 * 60 * 1_000
const TERMINAL_STATUSES: readonly RunStatus[] = ['succeeded', 'distributed', 'failed']

function messageOf(error: unknown, fallback: string): string {
  return extractApiError(error)?.message ?? fallback
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export const useRunsStore = defineStore('saas-a-billing/runs', () => {
  const runs = ref<RunSummary[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const targetEnvFilter = ref<TargetEnv | null>(null)
  const polling = new Set<string>()

  function isSame(a: RunSummary, month: string, targetEnv: TargetEnv): boolean {
    return a.month === month && a.target_env === targetEnv
  }

  function upsert(run: RunSummary): void {
    const found = runs.value.some((item) => isSame(item, run.month, run.target_env))
    runs.value = found
      ? runs.value.map((item) => (isSame(item, run.month, run.target_env) ? run : item))
      : [run, ...runs.value]
  }

  /** stale な queued/running はサーバが `failed` に読み替えて返すため、ここでは再判定しない（設計 8 章）。 */
  async function fetchRuns(targetEnv?: TargetEnv): Promise<void> {
    targetEnvFilter.value = targetEnv ?? null
    loading.value = true
    error.value = null
    try {
      runs.value = (await billingApi.listRuns({ targetEnv })) ?? []
    } catch (e: unknown) {
      error.value = messageOf(e, '実行履歴の取得に失敗しました')
    } finally {
      loading.value = false
    }
  }

  /** 署名付き URL は 300 秒で切れるため、取得したその場で開く（設計 8 章）。 */
  async function download(month: string, targetEnv: TargetEnv): Promise<void> {
    error.value = null
    try {
      const link = await billingApi.getDownloadUrl(month, targetEnv)
      if (link) {
        window.open(link.url, '_blank', 'noopener,noreferrer')
      }
    } catch (e: unknown) {
      error.value = messageOf(e, 'ダウンロードに失敗しました')
    }
  }

  async function createRun(payload: RunRequest): Promise<RunAccepted | null> {
    loading.value = true
    error.value = null
    try {
      const accepted = await billingApi.createRun(payload)
      await fetchRuns(targetEnvFilter.value ?? undefined)
      if (accepted) {
        void pollRun(accepted.month, accepted.target_env)
      }
      return accepted
    } catch (e: unknown) {
      error.value = messageOf(e, '実行の開始に失敗しました')
      return null
    } finally {
      loading.value = false
    }
  }

  /**
   * 終端状態に達するか、上限 10 分を過ぎたら止める。
   * 同じ月を画面から何度も開いても監視は 1 本に保つ。
   */
  async function pollRun(month: string, targetEnv: TargetEnv): Promise<void> {
    const key = `${targetEnv}#${month}`
    if (polling.has(key)) {
      return
    }
    polling.add(key)
    try {
      const deadline = Date.now() + POLL_TIMEOUT_MS
      while (Date.now() < deadline) {
        await sleep(POLL_INTERVAL_MS)
        try {
          const detail = await billingApi.getRun(month, { targetEnv })
          if (!detail) {
            continue
          }
          upsert(detail.run)
          if (TERMINAL_STATUSES.includes(detail.run.status)) {
            return
          }
        } catch (e: unknown) {
          error.value = messageOf(e, '実行状況の取得に失敗しました')
          return
        }
      }
    } finally {
      polling.delete(key)
    }
  }

  return { runs, loading, error, targetEnvFilter, fetchRuns, download, createRun, pollRun }
})
