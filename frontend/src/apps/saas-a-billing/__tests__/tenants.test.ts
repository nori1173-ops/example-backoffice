import { describe, expect, it } from 'vitest'
import { DEFAULT_EXCLUDE_KEYWORDS, isTenantExcluded } from '../utils/tenants'

describe('isTenantExcluded', () => {
  it('テナント説明が除外キーワードに一致すると true', () => {
    expect(isTenantExcluded({ tenant_description: 'デモ用テナント' }, ['デモ'])).toBe(true)
  })

  it('一致しなければ false', () => {
    expect(isTenantExcluded({ tenant_description: '本番運用' }, ['デモ'])).toBe(false)
  })

  it('説明が空なら false', () => {
    expect(isTenantExcluded({ tenant_description: '' }, ['デモ'])).toBe(false)
  })

  // billing.py の contains_any と同じ規則
  it('大文字小文字を無視した部分一致', () => {
    expect(isTenantExcluded({ tenant_description: 'DEMO tenant' }, ['demo'])).toBe(true)
    expect(isTenantExcluded({ tenant_description: '社内デモ用' }, ['デモ'])).toBe(true)
  })

  it('キーワードが空なら誰も除外しない', () => {
    expect(isTenantExcluded({ tenant_description: 'デモ用テナント' }, [])).toBe(false)
    expect(isTenantExcluded({ tenant_description: 'デモ用テナント' }, [''])).toBe(false)
  })

  // contains_any は falsy なキーワードを読み飛ばす。混ざっても落とさない
  it('null や undefined のキーワードは読み飛ばす', () => {
    const keywords = [null, undefined, 'デモ'] as unknown as string[]
    expect(isTenantExcluded({ tenant_description: 'デモ用テナント' }, keywords)).toBe(true)
    expect(isTenantExcluded({ tenant_description: '本番運用' }, keywords)).toBe(false)
  })

  it('既定の除外キーワードはバックエンドと同じ「デモ」', () => {
    expect(DEFAULT_EXCLUDE_KEYWORDS).toEqual(['デモ'])
  })
})
