import { defineStore } from 'pinia'
import { ref } from 'vue'
import { extractApiError } from '@/services/http'
import { billingApi } from '../api'
import type { RunAccepted, RunRequest, RunStatus, RunSummary, TargetEnv } from '../types'

const POLL_INTERVAL_MS = 5_000
const POLL_TIMEOUT_MS = 10 * 60 * 1_000
const TERMINAL_STATUSES: readonly RunStatus[] = ['succeeded', 'distributed', 'failed']

/** 409 の本文はコード名がそのまま入るため、画面向けの文言に置き換える（設計 8 章）。 */
const CONFLICT_MESSAGES: Record<string, string> = {
  ALREADY_SUCCEEDED:
    'この月は既に集計済みです。結果を作り直すには「強制再実行」にチェックを入れてください。',
  ALREADY_RUNNING: 'この月は実行中です。終わるまで待ってから実行してください。',
}

function messageOf(error: unknown, fallback: string): string {
  const api = extractApiError(error)
  if (!api) {
    return fallback
  }
  return CONFLICT_MESSAGES[api.code] ?? api.message
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export function runKey(month: string, targetEnv: TargetEnv): string {
  return `${targetEnv}#${month}`
}

/**
 * fetch/XHR を使わず遷移でダウンロードする（設計 R4）。
 * `await` を挟んだ `window.open` はポップアップブロックに当たる（UI-15）。
 */
function openDownload(url: string): void {
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.rel = 'noopener'
  anchor.download = ''
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

export const useRunsStore = defineStore('saas-a-billing/runs', () => {
  const runs = ref<RunSummary[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const downloading = ref<string | null>(null)
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

  /**
   * stale な queued/running はサーバが `failed` に読み替えて返すため、ここでは再判定しない（設計 8 章）。
   * 集計対象環境はサイト固定なので絞り込みは掛けない（設計 R1）。
   */
  async function fetchRuns(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      runs.value = (await billingApi.listRuns()) ?? []
    } catch (e: unknown) {
      error.value = messageOf(e, '実行履歴の取得に失敗しました')
    } finally {
      loading.value = false
    }
  }

  /** 署名付き URL は 300 秒で切れるため、取得したその場で開く（設計 8 章）。 */
  async function download(month: string, targetEnv: TargetEnv): Promise<void> {
    error.value = null
    downloading.value = runKey(month, targetEnv)
    try {
      const link = await billingApi.getDownloadUrl(month, targetEnv)
      if (link) {
        openDownload(link.url)
      }
    } catch (e: unknown) {
      error.value = messageOf(e, 'ダウンロードに失敗しました')
    } finally {
      downloading.value = null
    }
  }

  async function createRun(payload: RunRequest): Promise<RunAccepted | null> {
    loading.value = true
    error.value = null
    try {
      const accepted = await billingApi.createRun(payload)
      await fetchRuns()
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
    const key = runKey(month, targetEnv)
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

  return { runs, loading, error, downloading, fetchRuns, download, createRun, pollRun }
})
