export interface ApiError {
  code: string
  message: string
}

/** API 共通エンベロープ（設計 10.2 / shared `response.py`）。 */
export interface ApiEnvelope<T> {
  ok: boolean
  data: T | null
  error: ApiError | null
  meta: Record<string, unknown> | null
}
