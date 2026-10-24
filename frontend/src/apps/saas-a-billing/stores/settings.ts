import { defineStore } from 'pinia'
import { ref } from 'vue'
import { extractApiError } from '@/services/http'
import { billingApi } from '../api'
import type {
  EditableSettings,
  PriceTable,
  PriceVersion,
  SettingsView,
  SlackDirectory,
  SlackMode,
  TargetEnv,
} from '../types'

const VERSION_CONFLICT = 'VERSION_CONFLICT'
const TARGET_ENV_FIXED = 'TARGET_ENV_FIXED'

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

export const useSettingsStore = defineStore('saas-a-billing/settings', () => {
  const settings = ref<SettingsView | null>(null)
  const prices = ref<PriceVersion[]>([])
  const slackMode = ref<SlackMode | null>(null)
  const targets = ref<Partial<Record<TargetEnv, boolean>>>(noTargets())
  const saasaHost = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const directory = ref<SlackDirectory | null>(null)
  const directoryLoading = ref(false)
  const directoryError = ref<string | null>(null)

  async function fetch(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const [view, priceList] = await Promise.all([
        billingApi.getSettings(),
        billingApi.listPrices(),
      ])
      settings.value = view
        ? {
          ...view,
          mail_attachment_sheets: view.mail_attachment_sheets === 'all' ? 'all' : 'billing_only',
        }
        : null
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

  /**
   * Slack の候補（`#` / `@` の入力補助）。取得できなくても設定の編集と保存は続けられる
   * ように、失敗は例外にせず理由だけ持つ（#1011）。
   */
  async function loadSlackDirectory(refresh = false): Promise<void> {
    directoryLoading.value = true
    directoryError.value = null
    try {
      directory.value = await billingApi.getSlackDirectory(refresh)
    } catch (e: unknown) {
      directoryError.value = messageOf(e, '通信に失敗しました')
    } finally {
      directoryLoading.value = false
    }
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

  return {
    settings,
    prices,
    slackMode,
    targets,
    saasaHost,
    loading,
    error,
    directory,
    directoryLoading,
    directoryError,
    fetch,
    save,
    addPrice,
    loadSlackDirectory,
  }
})
