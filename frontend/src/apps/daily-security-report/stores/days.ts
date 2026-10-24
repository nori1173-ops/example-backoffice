import { defineStore } from 'pinia'
import { ref } from 'vue'
import { extractApiError } from '@/services/http'
import { reportApi } from '../api'
import type { DayDetail, DaySummary } from '../types'

export const useDaysStore = defineStore('daily-security-report/days', () => {
  const days = ref<DaySummary[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)
  const details = ref<Record<string, DayDetail>>({})
  const detailLoading = ref<Record<string, boolean>>({})
  const detailErrors = ref<Record<string, string | null>>({})
  const detailNotFound = ref<Record<string, boolean>>({})
  const pending = new Map<string, Promise<void>>()
  function fetchDay(date: string, refresh = false): Promise<void> {
    const active = pending.get(date)
    if (active) return active
    if (!refresh && details.value[date]?.status === 'succeeded') return Promise.resolve()
    detailLoading.value[date] = true
    detailErrors.value[date] = null
    detailNotFound.value[date] = false
    const request = (async () => {
      try {
        const detail = await reportApi.getDay(date)
        if (!detail) throw new Error('empty detail')
        details.value[date] = detail
      } catch (e: unknown) {
        const error = extractApiError(e)
        const status = (e as { response?: { status?: number } })?.response?.status
        detailNotFound.value[date] = error?.code === 'NOT_FOUND' || status === 404
        detailErrors.value[date] = detailNotFound.value[date] ? 'レポートがありません'
          : error?.message ?? 'レポートの取得に失敗しました'
      } finally {
        detailLoading.value[date] = false
        pending.delete(date)
      }
    })()
    pending.set(date, request)
    return request
  }
  async function fetch(limit = 30): Promise<void> {
    loading.value = true
    error.value = null
    try {
      days.value = await reportApi.listDays(limit) ?? []
    } catch (e: unknown) {
      error.value = extractApiError(e)?.message ?? 'レポート一覧の取得に失敗しました'
    } finally {
      loading.value = false
    }
  }
  return { days, loading, error, fetch, details, detailLoading, detailErrors, detailNotFound, fetchDay }
})
