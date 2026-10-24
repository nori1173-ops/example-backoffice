import { afterEach, describe, expect, it, vi } from 'vitest'
import { effectivePrompt, newId, PRESET_TOTAL_MAX_CHARS, presetNameError, presetTotalChars, promptExcerpt, samePrompt } from '../utils/prompt'

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
const preset = (id: string, name: string) => ({ id, name, prompt: `${name}の本文`, updated_at: '2026-10-16T13:00:00+09:00' })

afterEach(() => { vi.unstubAllGlobals() })

describe('newId', () => {
  it('randomUUID があればその値を使う', () => {
    vi.stubGlobal('crypto', { randomUUID: () => '0f0e0d0c-0b0a-4908-8706-050403020100', getRandomValues: () => { throw new Error('unused') } })
    expect(newId()).toBe('0f0e0d0c-0b0a-4908-8706-050403020100')
  })
  it.each([
    [0xff, 'ffffffff-ffff-4fff-bfff-ffffffffffff'],
    [0x00, '00000000-0000-4000-8000-000000000000'],
  ])('randomUUID が無いときは getRandomValues から v4 の小文字 UUID を組み立てる（%i）', (fill, expected) => {
    vi.stubGlobal('crypto', { getRandomValues: (bytes: Uint8Array) => bytes.fill(fill) })
    expect(newId()).toBe(expected)
    expect(newId()).toMatch(UUID_V4)
  })
  it('既定の環境で毎回異なる v4 を返す', () => {
    const ids = new Set(Array.from({ length: 50 }, () => newId()))
    expect(ids.size).toBe(50)
    for (const id of ids) expect(id).toMatch(UUID_V4)
  })
})

describe('effectivePrompt', () => {
  it.each(['既定の本文です。', '  既定の本文です。\n', '', '   \n'])('既定本文と trim 後に同じ・空白だけ（%j）は空にする', text => {
    expect(effectivePrompt(text, '既定の本文です。')).toBe('')
  })
  it('違う文面はそのまま返す', () => {
    expect(effectivePrompt('既定の本文です。追記', '既定の本文です。')).toBe('既定の本文です。追記')
    expect(effectivePrompt(' 別の文面\n', '既定の本文です。')).toBe(' 別の文面\n')
  })
  it('既定本文が未取得でも文面を保つ', () => {
    expect(effectivePrompt('別の文面', '')).toBe('別の文面')
  })
  it('samePrompt は前後の空白を無視して比べる', () => {
    expect(samePrompt(' 文面\n', '文面')).toBe(true)
    expect(samePrompt('文面', '文面。')).toBe(false)
  })
})

describe('promptExcerpt', () => {
  it('120 字までは全文、超えたら先頭 120 字に省略記号を付ける', () => {
    expect(promptExcerpt('短い文面')).toBe('短い文面')
    expect(promptExcerpt('あ'.repeat(120))).toBe('あ'.repeat(120))
    expect(promptExcerpt('あ'.repeat(130))).toBe(`${'あ'.repeat(120)}…`)
    expect(promptExcerpt('😀'.repeat(121))).toBe(`${'😀'.repeat(120)}…`)
  })
})

describe('presetTotalChars', () => {
  it('本文を trim し code point で数えて合計する', () => {
    expect(PRESET_TOTAL_MAX_CHARS).toBe(20000)
    expect(presetTotalChars([])).toBe(0)
    expect(presetTotalChars([
      { ...preset('a', '案 1'), prompt: '  あいう\n' },
      { ...preset('b', '案 2'), prompt: '😀😀' },
    ])).toBe(5)
  })
})

describe('presetNameError', () => {
  const presets = [preset('11111111-1111-4111-8111-111111111111', '本番用'), preset('22222222-2222-4222-8222-222222222222', '検証用')]
  it.each(['', '   '])('空（%j）は必須', name => {
    expect(presetNameError(name, presets)).toBe('名前を入力してください')
  })
  it('trim 後 40 字までを許し、41 字は拒否する', () => {
    expect(presetNameError(` ${'名'.repeat(40)} `, presets)).toBe('')
    expect(presetNameError('😀'.repeat(40), presets)).toBe('')
    expect(presetNameError('名'.repeat(41), presets)).toBe('名前は 40 字以内にしてください')
  })
  it('既存の名前と trim 後に重複したら拒否し、自分自身の名前は許す', () => {
    expect(presetNameError(' 本番用 ', presets)).toBe('同じ名前のカードがあります')
    expect(presetNameError('本番用', presets, presets[0].id)).toBe('')
    expect(presetNameError('検証用', presets, presets[0].id)).toBe('同じ名前のカードがあります')
    expect(presetNameError('新しい案', presets)).toBe('')
  })
})
