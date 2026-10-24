import { defineStore } from 'pinia'
import { ref } from 'vue'
import { extractApiError } from '@/services/http'
import { reportApi } from '../api'
import type { EditableSettings, SettingsView, SlackDirectory } from '../types'

export const useSettingsStore = defineStore('daily-security-report/settings', () => {
  const settings = ref<SettingsView | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const directory = ref<SlackDirectory | null>(null)
  const directoryLoading = ref(false)
  const directoryError = ref<string | null>(null)

  async function fetch(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const view = await reportApi.getSettings()
      if (!view) throw new Error('empty settings')
      settings.value = view
    } catch (e: unknown) {
      error.value = extractApiError(e)?.message ?? '設定の取得に失敗しました'
    } finally {
      loading.value = false
    }
  }
  async function save(patch: Partial<EditableSettings>): Promise<boolean> {
    if (!settings.value || loading.value) return false
    loading.value = true
    error.value = null
    try {
      const saved = await reportApi.updateSettings({ ...patch, version: settings.value.version })
      if (!saved) throw new Error('empty settings')
      settings.value = { ...settings.value, ...saved }
      return true
    } catch (e: unknown) {
      const detail = extractApiError(e)
      error.value = detail?.code === 'VERSION_CONFLICT'
        ? '他の人が更新しました。「変更を破棄」で最新の設定を読み込み直してから保存してください。'
        : detail?.message ?? '設定の保存に失敗しました'
      return false
    } finally {
      loading.value = false
    }
  }
  async function loadSlackDirectory(refresh = false): Promise<void> {
    directoryLoading.value = true
    directoryError.value = null
    try {
      directory.value = await reportApi.getSlackDirectory(refresh)
    } catch (e: unknown) {
      directoryError.value = extractApiError(e)?.message ?? '候補の取得に失敗しました'
    } finally {
      directoryLoading.value = false
    }
  }
  return { settings, loading, error, directory, directoryLoading, directoryError, fetch, save, loadSlackDirectory }
})
