import type { TenantResult } from '../types'

/** `settings_snapshot` を持たない古い result.json 向けの既定（設定の既定値と同じ）。 */
export const DEFAULT_EXCLUDE_KEYWORDS = ['デモ']

/** billing.py の `contains_any` と同じ規則（大文字小文字を無視した部分一致）。 */
export function isTenantExcluded(
  tenant: Pick<TenantResult, 'tenant_description'>,
  keywords: string[],
): boolean {
  const description = (tenant.tenant_description ?? '').toLowerCase()
  return (
    description !== '' &&
    keywords.some((keyword) => Boolean(keyword) && description.includes(keyword.toLowerCase()))
  )
}
