import { createAppApi } from '@/services/http'
import type { ApiEnvelope } from '@/types'

// baseURL は /api/{app}。Bearer 付与と 401 の後始末は createAppApi が行う（設計 9.7）。
const api = createAppApi('sample-app')

export interface HealthStatus {
  status: string
}

export async function fetchHealth(): Promise<HealthStatus | null> {
  const response = await api.get<ApiEnvelope<HealthStatus>>('/health')
  return response.data.data
}
