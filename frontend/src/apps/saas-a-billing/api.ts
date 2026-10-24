import { createAppApi } from '@/services/http'
import type { ApiEnvelope } from '@/types'
import type {
  AuditEntry,
  AuditTarget,
  DownloadUrl,
  PriceList,
  PriceTable,
  PriceVersion,
  RunAccepted,
  RunDetail,
  RunRequest,
  RunSummary,
  Settings,
  SettingsUpdate,
  SettingsView,
  SlackDirectory,
  TargetEnv,
} from './types'

// baseURL は /api/saas-a-billing。Bearer 付与と 401 の後始末は createAppApi が行う（設計 9.7）。
const api = createAppApi('saas-a-billing')

export interface RunListParams {
  targetEnv?: TargetEnv
  limit?: number
}

export interface RunDetailParams {
  targetEnv?: TargetEnv
  detail?: boolean
}

export interface AuditListParams {
  limit?: number
  target?: AuditTarget
  since?: string
}

export const billingApi = {
  async getSettings(): Promise<SettingsView | null> {
    const response = await api.get<ApiEnvelope<SettingsView>>('/settings')
    return response.data.data
  },

  async updateSettings(settings: SettingsUpdate): Promise<Settings | null> {
    const response = await api.put<ApiEnvelope<Settings>>('/settings', settings)
    return response.data.data
  },

  async listPrices(): Promise<PriceList | null> {
    const response = await api.get<ApiEnvelope<PriceList>>('/prices')
    return response.data.data
  },

  async createPrice(price: PriceTable): Promise<PriceVersion | null> {
    const response = await api.post<ApiEnvelope<PriceVersion>>('/prices', price)
    return response.data.data
  },

  /** `limit` はサーバ側で 100 に丸められる。返却件数で打ち切りを判定しない。 */
  async listRuns(params: RunListParams = {}): Promise<RunSummary[] | null> {
    const response = await api.get<ApiEnvelope<RunSummary[]>>('/runs', {
      params: { target_env: params.targetEnv, limit: params.limit },
    })
    return response.data.data
  },

  /** `detail` の既定は API 側が false。`result.json` が要るときだけ明示する。 */
  async getRun(month: string, params: RunDetailParams = {}): Promise<RunDetail | null> {
    const response = await api.get<ApiEnvelope<RunDetail>>(`/runs/${month}`, {
      params: { target_env: params.targetEnv, ...(params.detail ? { detail: true } : {}) },
    })
    return response.data.data
  },

  /** `target_env` はサイトごとに固定（設計 R1）。指定されたときだけ載せる。 */
  async createRun(request: RunRequest): Promise<RunAccepted | null> {
    const response = await api.post<ApiEnvelope<RunAccepted>>('/runs', {
      month: request.month,
      ...(request.targetEnv ? { target_env: request.targetEnv } : {}),
      distribute: request.distribute,
      force: request.force,
    })
    return response.data.data
  },

  /** 署名付き URL は短命なので、取得したらそのまま開く（設計 8 章）。 */
  async getDownloadUrl(month: string, targetEnv?: TargetEnv): Promise<DownloadUrl | null> {
    const response = await api.get<ApiEnvelope<DownloadUrl>>(`/runs/${month}/download`, {
      params: { target_env: targetEnv },
    })
    return response.data.data
  },

  /** admin のみ（#1011）。`refresh` はサーバのキャッシュ（300 秒）を捨てて取り直す。 */
  async getSlackDirectory(refresh = false): Promise<SlackDirectory | null> {
    const response = await api.get<ApiEnvelope<SlackDirectory>>('/slack/directory', {
      params: refresh ? { refresh: 1 } : {},
    })
    return response.data.data
  },

  /** admin のみ（設計 12.1）。`since` は ISO 8601（`Z` 付きでよい）。 */
  async listAudits(params: AuditListParams = {}): Promise<AuditEntry[] | null> {
    const response = await api.get<ApiEnvelope<AuditEntry[]>>('/audits', {
      params: { limit: params.limit, target: params.target, since: params.since },
    })
    return response.data.data
  },
}
