import { describe, expect, it } from 'vitest'
import {
  DASH,
  formatCount,
  formatDate,
  formatDateTime,
  formatDateTimeSeconds,
  formatYen,
  toWallClock,
} from './format'

describe('format', () => {
  it('日時を JST の YYYY-MM-DD HH:mm にする', () => {
    expect(formatDateTime('2026-08-31T23:50:00+09:00')).toBe('2026-08-31 23:50')
  })

  it('日時は実行環境の時差に影響されない', () => {
    expect(formatDateTime('2026-01-01T00:05:00+09:00')).toBe('2026-01-01 00:05')
  })

  it('秒まで要る場所では YYYY-MM-DD HH:mm:ss にする', () => {
    expect(formatDateTimeSeconds('2026-09-21T00:00:00+09:00')).toBe('2026-09-21 00:00:00')
  })

  it('Date もそのまま整形できる', () => {
    expect(formatDateTime(new Date(2026, 8, 21, 6, 0))).toBe('2026-09-21 06:00')
    expect(formatDate(new Date(2026, 8, 21))).toBe('2026-09-21')
  })

  it('未設定はダッシュ', () => {
    expect(formatDateTime(null)).toBe(DASH)
    expect(formatDateTime(undefined)).toBe(DASH)
    expect(formatDateTimeSeconds(null)).toBe(DASH)
  })

  it('解釈できない文字列はそのまま返す', () => {
    expect(formatDateTime('いつか')).toBe('いつか')
  })

  it('金額は ¥ と桁区切り', () => {
    expect(formatYen(1234567)).toBe('¥1,234,567')
    expect(formatYen(0)).toBe('¥0')
    expect(formatYen(undefined)).toBe(DASH)
  })

  it('件数は桁区切りのみ', () => {
    expect(formatCount(1234)).toBe('1,234')
    expect(formatCount(0)).toBe('0')
    expect(formatCount(undefined)).toBe(DASH)
  })

  it('toWallClock は JST の壁時計を返す', () => {
    const date = toWallClock('2026-10-21T00:00:00+09:00')
    expect(date?.getFullYear()).toBe(2026)
    expect(date?.getMonth()).toBe(9)
    expect(date?.getDate()).toBe(21)
    expect(date?.getHours()).toBe(0)
    expect(toWallClock('x')).toBeNull()
  })
})
