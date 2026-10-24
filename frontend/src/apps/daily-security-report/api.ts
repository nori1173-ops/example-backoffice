import { createAppApi } from '@/services/http'
import type { ApiEnvelope } from '@/types'
import type { DayDetail, DaySummary, LogQueryRequest, LogQueryResponse, PreviewRequest, PreviewResult, RunAccepted, Settings, SettingsUpdate, SettingsView, SlackDirectory } from './types'

const api = createAppApi('daily-security-report')

export const reportApi = {
  async getDay(date: string): Promise<DayDetail | null> {
    return (await api.get<ApiEnvelope<DayDetail>>(`/days/${encodeURIComponent(date)}`)).data.data
  },
  async runDay(date: string): Promise<RunAccepted | null> {
    return (await api.post<ApiEnvelope<RunAccepted>>(`/days/${encodeURIComponent(date)}/run`)).data.data
  },
  async startLogs(date: string, body: LogQueryRequest): Promise<LogQueryResponse | null> {
    return (await api.post<ApiEnvelope<LogQueryResponse>>(`/days/${encodeURIComponent(date)}/logs`, body)).data.data
  },
  async getLogs(date: string, id: string): Promise<LogQueryResponse | null> {
    return (await api.get<ApiEnvelope<LogQueryResponse>>(`/days/${encodeURIComponent(date)}/logs/${encodeURIComponent(id)}`)).data.data
  },
  async startPreview(body: PreviewRequest): Promise<{ preview_id: string } | null> {
    return (await api.post<ApiEnvelope<{ preview_id: string }>>('/ai/preview', body)).data.data
  },
  async getPreview(id: string): Promise<PreviewResult | null> {
    return (await api.get<ApiEnvelope<PreviewResult>>(`/ai/preview/${encodeURIComponent(id)}`)).data.data
  },
  async getSettings(): Promise<SettingsView | null> {
    return (await api.get<ApiEnvelope<SettingsView>>('/settings')).data.data
  },
  async updateSettings(settings: SettingsUpdate): Promise<Settings | null> {
    return (await api.put<ApiEnvelope<Settings>>('/settings', settings)).data.data
  },
  async getSlackDirectory(refresh = false): Promise<SlackDirectory | null> {
    return (await api.get<ApiEnvelope<SlackDirectory>>('/slack/directory', {
      params: refresh ? { refresh: 1 } : {},
    })).data.data
  },
  async listDays(limit = 30): Promise<DaySummary[] | null> {
    return (await api.get<ApiEnvelope<DaySummary[]>>('/days', { params: { limit } })).data.data
  },
}
