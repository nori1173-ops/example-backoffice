import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { extractApiError } from '@/services/http'
import { billingApi } from '../api'
import type { AuditEntry, AuditTarget } from '../types'
import { summarize } from '../utils/audit'

export type LogRange = '7d' | '30d' | '90d' | 'all'

/** すべての期間は `since` を送らない。API 側が新しい順に `LIST_LIMIT` 件で打ち切る。 */
const RANGE_DAYS: Record<LogRange, number | null> = { '7d': 7, '30d': 30, '90d': 90, all: null }
const DAY_MS = 24 * 60 * 60 * 1000
const LIST_LIMIT = 200

function messageOf(error: unknown, fallback: string): string {
  return extractApiError(error)?.message ?? fallback
}

export const useLogsStore = defineStore('saas-a-billing/logs', () => {
  const entries = ref<AuditEntry[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const range = ref<LogRange>('30d')
  const target = ref<'' | AuditTarget>('')
  const query = ref('')

  function since(): string | undefined {
    const days = RANGE_DAYS[range.value]
    return days === null ? undefined : new Date(Date.now() - days * DAY_MS).toISOString()
  }

  async function fetch(): Promise<void> {
    loading.value = true
    error.value = null
    const params = { limit: LIST_LIMIT, target: target.value || undefined, since: since() }
    try {
      entries.value = (await billingApi.listAudits(params)) ?? []
    } catch (e: unknown) {
      entries.value = []
      error.value = messageOf(e, '操作ログの取得に失敗しました')
    } finally {
      loading.value = false
    }
  }

  /** 期間と種別はサーバ側で絞る。検索だけは取得済みの中で絞る（打ち直しのたびに待たせない）。 */
  const filtered = computed(() => {
    const needle = query.value.trim().toLowerCase()
    if (!needle) {
      return entries.value
    }
    return entries.value.filter((entry) =>
      `${entry.actor_email} ${summarize(entry)}`.toLowerCase().includes(needle),
    )
  })

  return { entries, loading, error, range, target, query, filtered, fetch }
})
