import { defineStore } from 'pinia'
import { ref } from 'vue'
import { extractApiError } from '@/services/http'
import { billingApi } from '../api'
import type {
  AuditEntry,
  EditableSettings,
  PriceTable,
  PriceVersion,
  SettingsView,
  SlackMode,
  TargetEnv,
} from '../types'

const VERSION_CONFLICT = 'VERSION_CONFLICT'
const TARGET_ENV_FIXED = 'TARGET_ENV_FIXED'
const FORBIDDEN = 403

const SAVE_ERRORS: Record<string, string> = {
  [VERSION_CONFLICT]: '他の人が更新しました。画面を読み込み直してから保存してください。',
  [TARGET_ENV_FIXED]:
    '集計対象環境はこのサイトで固定されています。画面を読み込み直してから保存してください。',
}

function noTargets(): Partial<Record<TargetEnv, boolean>> {
  return {}
}

function messageOf(error: unknown, fallback: string): string {
  return extractApiError(error)?.message ?? fallback
}

function statusOf(error: unknown): number | undefined {
  if (error && typeof error === 'object' && 'response' in error) {
    return (error as { response?: { status?: number } }).response?.status
  }
  return undefined
}

export const useSettingsStore = defineStore('saas-a-billing/settings', () => {
  const settings = ref<SettingsView | null>(null)
  const prices = ref<PriceVersion[]>([])
  const audits = ref<AuditEntry[]>([])
  const slackMode = ref<SlackMode | null>(null)
  const targets = ref<Partial<Record<TargetEnv, boolean>>>(noTargets())
  const saasaHost = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetch(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const [view, priceList] = await Promise.all([
        billingApi.getSettings(),
        billingApi.listPrices(),
      ])
      settings.value = view
      slackMode.value = view?.slack_mode ?? null
      targets.value = view?.targets ?? noTargets()
      saasaHost.value = view?.saasa_host ?? null
      prices.value = priceList?.versions ?? []
    } catch (e: unknown) {
      error.value = messageOf(e, '設定の取得に失敗しました')
    } finally {
      loading.value = false
    }
  }

  /**
   * 楽観ロックの `version` は保存中の設定のものを載せる（設計 8 章）。
   * `target_env` はサイト固定で送らない（設計 R1）。成功時は SSM 由来の項目ごと取り直す。
   */
  async function save(patch: Partial<EditableSettings>): Promise<boolean> {
    if (!settings.value) {
      return false
    }
    loading.value = true
    error.value = null
    try {
      await billingApi.updateSettings({ ...patch, version: settings.value.version })
    } catch (e: unknown) {
      const code = extractApiError(e)?.code ?? ''
      error.value = SAVE_ERRORS[code] ?? messageOf(e, '設定の保存に失敗しました')
      return false
    } finally {
      loading.value = false
    }
    await fetch()
    return true
  }

  async function addPrice(version: PriceTable): Promise<boolean> {
    loading.value = true
    error.value = null
    try {
      await billingApi.createPrice(version)
      prices.value = (await billingApi.listPrices())?.versions ?? []
      return true
    } catch (e: unknown) {
      error.value = messageOf(e, '単価表の追加に失敗しました')
      return false
    } finally {
      loading.value = false
    }
  }

  /** admin 以外は 403 になる。閲覧者に見せる情報ではないのでエラー表示はしない（設計 12.1）。 */
  async function fetchAudits(): Promise<void> {
    try {
      audits.value = (await billingApi.listAudits()) ?? []
    } catch (e: unknown) {
      if (statusOf(e) !== FORBIDDEN) {
        error.value = messageOf(e, '変更履歴の取得に失敗しました')
      }
    }
  }

  return {
    settings,
    prices,
    audits,
    slackMode,
    targets,
    saasaHost,
    loading,
    error,
    fetch,
    save,
    addPrice,
    fetchAudits,
  }
})
