import { describe, expect, it } from 'vitest'
import {
  TOP_SECTIONS, counterRows, counterSection, evidenceNames, formatBaseline, formatRatio, formatTimeRange, hourlyBreakdown, isConsoleLink, peakHours,
} from '../utils/trace'
import type { Finding } from '../types'

describe('formatTimeRange', () => {
  it('開始と終了を HH:MM〜HH:MM で出す', () => {
    expect(formatTimeRange('2026-10-14T10:00:00+09:00', '2026-10-14T10:05:00+09:00')).toBe('10:00〜10:05')
  })
  it('同じ時刻なら 1 つ、片方だけならその時刻、無ければ —', () => {
    expect(formatTimeRange('2026-10-14T10:00:00+09:00', '2026-10-14T10:00:00+09:00')).toBe('10:00')
    expect(formatTimeRange('2026-10-14T03:07:00+09:00', undefined)).toBe('03:07')
    expect(formatTimeRange(undefined, '2026-10-14T23:59:00+09:00')).toBe('23:59')
    expect(formatTimeRange(undefined, undefined)).toBe('—')
    expect(formatTimeRange(null, '')).toBe('—')
  })
  it('解釈できない文字列はそのまま出す', () => {
    expect(formatTimeRange('昨日', undefined)).toBe('昨日')
  })
})

describe('isConsoleLink', () => {
  it('https の AWS コンソールだけを許す', () => {
    expect(isConsoleLink('https://ap-northeast-1.console.aws.amazon.com/guardduty/home?region=ap-northeast-1#/findings?fId=abc')).toBe(true)
    expect(isConsoleLink('https://console.aws.amazon.com/cloudtrailv2/home')).toBe(true)
  })
  it('http・別ホスト・文字列以外・ホスト名偽装は許さない', () => {
    expect(isConsoleLink('http://console.aws.amazon.com/x')).toBe(false)
    expect(isConsoleLink('https://example.com/?u=console.aws.amazon.com')).toBe(false)
    expect(isConsoleLink('https://console.aws.amazon.com.example.com/x')).toBe(false)
    expect(isConsoleLink('javascript:alert(1)')).toBe(false)
    expect(isConsoleLink(undefined)).toBe(false)
    expect(isConsoleLink(12)).toBe(false)
    expect(isConsoleLink({ url: 'https://console.aws.amazon.com/' })).toBe(false)
  })
})

describe('peakHours / hourlyBreakdown', () => {
  const hourly = Array.from({ length: 24 }, (_, hour) => (hour === 3 ? 80 : hour === 17 ? 12 : hour === 9 ? 5 : 0))
  it('最多の時間帯を 2 つまで出す', () => {
    expect(peakHours(hourly)).toBe('03 時 80 件、17 時 12 件')
    expect(peakHours(hourly, 1)).toBe('03 時 80 件')
  })
  it('全部 0・配列でない・数以外は —', () => {
    expect(peakHours(new Array(24).fill(0))).toBe('—')
    expect(peakHours(undefined)).toBe('—')
    expect(peakHours('03')).toBe('—')
    expect(peakHours([1, 'x'])).toBe('—')
  })
  it('同数は早い時間帯を先に出し、1,000 以上は桁区切り', () => {
    expect(peakHours([0, 1200, 0, 1200])).toBe('01 時 1,200 件、03 時 1,200 件')
  })
  it('内訳は 24 時間を順に並べる', () => {
    const text = hourlyBreakdown(hourly)
    expect(text.startsWith('00 時 0 件 / 01 時 0 件')).toBe(true)
    expect(text).toContain('03 時 80 件')
    expect(text.endsWith('23 時 0 件')).toBe(true)
    expect(hourlyBreakdown(undefined)).toBe('')
  })
})

describe('formatBaseline', () => {
  it('小数は 1 桁に丸め、整数は整数のまま桁区切り、null は —', () => {
    expect(formatBaseline(180.571)).toBe('180.6')
    expect(formatBaseline(97)).toBe('97')
    expect(formatBaseline(1234.44)).toBe('1,234.4')
    expect(formatBaseline(180.04)).toBe('180')
    expect(formatBaseline(0.25)).toBe('0.3')
    expect(formatBaseline(null)).toBe('—')
    expect(formatBaseline(undefined)).toBe('—')
  })
})

describe('formatRatio', () => {
  it('×1.29 の形、null は —', () => {
    expect(formatRatio(1.2857)).toBe('×1.29')
    expect(formatRatio(3)).toBe('×3.00')
    expect(formatRatio(null)).toBe('—')
    expect(formatRatio(undefined)).toBe('—')
  })
})

describe('counterSection / TOP_SECTIONS', () => {
  it('集計値のキーを区分に割り当てる', () => {
    expect(counterSection('waf.blocked')).toBe('metrics')
    expect(counterSection('cloudfront.requests.E1XXXX')).toBe('metrics')
    expect(counterSection('lambda.errors')).toBe('metrics')
    expect(counterSection('alarmhuba.direct')).toBe('insights')
    expect(counterSection('gps.received')).toBe('insights')
    expect(counterSection('guardduty.new.high')).toBe('guardduty')
    expect(counterSection('cloudtrail.root')).toBe('cloudtrail')
    expect(counterSection('instances.ec2.i-1.cpu_avg')).toBe('instances')
    for (const key of ['s3.buckets', 's3.objects', 's3.bytes_downloaded', 's3.delete_requests', 's3.get_requests']) expect(counterSection(key)).toBe('s3')
    expect(counterSection('unknown.key')).toBeNull()
  })
  it('上位表を区分に割り当てる', () => {
    expect(TOP_SECTIONS.guardduty).toBe('guardduty')
    expect(TOP_SECTIONS.access_denied_principals).toBe('cloudtrail')
    expect(TOP_SECTIONS.console_login_failures).toBe('cloudtrail')
    for (const key of ['waf_countries', 'waf_vulnerabilities', 'waf_rules', 'waf_dimensions', 'lambda_errors']) expect(TOP_SECTIONS[key]).toBe('metrics')
    for (const key of ['alarmhuba_statuses', 'alarmhuba_ips', 'alarmhuba_countries', 'gps_ips']) expect(TOP_SECTIONS[key]).toBe('insights')
  })
})

describe('counterRows', () => {
  const glossary = { 'waf.blocked': { label: 'WAF 遮断', description: 'Web ACL が遮断したリクエスト' } }
  const comparisons = {
    'waf.blocked': { value: 231, baseline: 180, days: 7, exceeded: true, ratio: 1.2833, label: '' },
    'alb.4xx': { value: 3, baseline: null, days: 1, exceeded: false, ratio: null, label: '基準なし' },
  }
  it('表示名・説明・基準・比率・時間帯・AI の根拠をまとめる', () => {
    const rows = counterRows({ counters: { 'waf.blocked': 231, 'alb.4xx': 3, 'alb.5xx': 0 }, comparisons, glossary,
      hourly: { 'waf.blocked': Array.from({ length: 24 }, (_, hour) => (hour === 3 ? 200 : 1)) }, aiKeys: ['waf.blocked', 'ADMIN_EXTERNAL'] })
    expect(rows.map(row => row.key)).toEqual(['waf.blocked', 'alb.4xx', 'alb.5xx'])
    expect(rows[0]).toMatchObject({ label: 'WAF 遮断', description: 'Web ACL が遮断したリクエスト', value: 231, ai: true })
    expect(rows[0]!.comparison?.exceeded).toBe(true)
    expect(rows[0]!.hourly?.[3]).toBe(200)
    expect(rows[1]).toMatchObject({ label: 'alb.4xx', description: '', value: 3, ai: false, hourly: null })
    expect(rows[1]!.comparison?.label).toBe('基準なし')
    expect(rows[2]).toMatchObject({ label: 'alb.5xx', comparison: null, hourly: null, ai: false })
  })
  it('glossary・comparisons・hourly が無い旧記録でも落ちない', () => {
    const rows = counterRows({ counters: { 'waf.blocked': 231 } })
    expect(rows).toEqual([{ key: 'waf.blocked', label: 'waf.blocked', description: '', value: 231, comparison: null, hourly: null, ai: false }])
  })
  it('counters が無ければ空', () => {
    expect(counterRows({})).toEqual([])
  })
})

describe('evidenceNames', () => {
  const findings: Finding[] = [{ section: 'insights', code: 'ADMIN_EXTERNAL', level: 'warning', text: '198.51.100.7（US）/api/admin', count: 12 }]
  it('指標キーは表示名、所見コードは所見の内容、上位表は見出し、無いキーはそのまま', () => {
    expect(evidenceNames(['waf.blocked', 'ADMIN_EXTERNAL', 'lambda_errors', 'mystery'], { 'waf.blocked': { label: 'WAF 遮断', description: '' } }, findings))
      .toEqual(['WAF 遮断', '198.51.100.7（US）/api/admin', 'Lambda エラーの関数（上位）', 'mystery'])
  })
  it('keys が無い・空・文字列以外を含むときは安全に扱う', () => {
    expect(evidenceNames(undefined, undefined, [])).toEqual([])
    expect(evidenceNames([], undefined, [])).toEqual([])
    expect(evidenceNames(['a', 7 as unknown as string, '', 'b'], undefined, [])).toEqual(['a', 'b'])
  })
})
