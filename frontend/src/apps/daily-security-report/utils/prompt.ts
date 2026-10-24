import type { PromptPreset } from '../types'

export const PROMPT_MAX_CHARS = 8000
export const PRESET_LIMIT = 20
export const PRESET_NAME_MAX_CHARS = 40
export const PRESET_TOTAL_MAX_CHARS = 20000
const EXCERPT_CHARS = 120

export const charCount = (text: string): number => [...text].length
export const formatChars = (count: number): string => count.toLocaleString('en-US')

export function presetTotalChars(presets: readonly PromptPreset[]): number {
  return presets.reduce((total, preset) => total + charCount(preset.prompt.trim()), 0)
}

export function samePrompt(a: string, b: string): boolean {
  return a.trim() === b.trim()
}

export function effectivePrompt(text: string, defaultText: string): string {
  return !text.trim() || samePrompt(text, defaultText) ? '' : text
}

export function promptExcerpt(text: string): string {
  const chars = [...text]
  return chars.length > EXCERPT_CHARS ? `${chars.slice(0, EXCERPT_CHARS).join('')}…` : text
}

export function presetNameError(name: string, presets: readonly PromptPreset[], exceptId?: string): string {
  const trimmed = name.trim()
  if (!trimmed) return '名前を入力してください'
  if (charCount(trimmed) > PRESET_NAME_MAX_CHARS) return `名前は ${PRESET_NAME_MAX_CHARS} 字以内にしてください`
  if (presets.some(preset => preset.id !== exceptId && preset.name.trim() === trimmed)) return '同じ名前のカードがあります'
  return ''
}

export function newId(): string {
  const source = globalThis.crypto
  if (typeof source?.randomUUID === 'function') return source.randomUUID().toLowerCase()
  const bytes = source.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
