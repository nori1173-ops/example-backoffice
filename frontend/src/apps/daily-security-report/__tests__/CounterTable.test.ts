import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import HelpTip from '@/components/HelpTip.vue'
import CounterTable from '../components/CounterTable.vue'
import type { Comparison, Glossary } from '../types'

let wrapper: VueWrapper
const glossary: Glossary = {
  'waf.blocked': { label: 'WAF 遮断', description: 'Web ACL（backoffice 等）が遮断したリクエスト' },
  'alb.4xx': { label: 'ALB 4xx', description: '登録した ALB の 4xx 応答' },
}
const comparisons: Record<string, Comparison> = {
  'waf.blocked': { value: 231, baseline: 180.571, days: 7, exceeded: true, ratio: 1.2833, label: '' },
  'alb.4xx': { value: 3, baseline: null, days: 1, exceeded: false, ratio: null, label: '基準なし' },
}
const hourly = { 'waf.blocked': Array.from({ length: 24 }, (_, hour) => (hour === 3 ? 200 : hour === 17 ? 20 : 1)) }
function mountTable(props: Partial<InstanceType<typeof CounterTable>['$props']> = {}) {
  wrapper = mount(CounterTable, {
    props: { label: '入口の指標 の集計値', counters: { 'waf.blocked': 231, 'alb.4xx': 3, 'alb.5xx': 0 }, comparisons, hourly, glossary, aiKeys: ['waf.blocked'], ...props },
    global: { plugins: [createPinia(), createVuetify({ components, directives })] },
  })
  return wrapper
}
afterEach(() => wrapper?.unmount())

describe('CounterTable', () => {
  it('列見出しと表示名・キー・件数・基準・比率・判定・時間帯を出す', () => {
    const table = mountTable()
    expect(table.findAll('thead th').map(th => th.text())).toEqual(['指標', '件数', '基準', '比率', '判定', '時間帯'])
    const rows = table.findAll('[data-test="counter-row"]')
    expect(rows).toHaveLength(3)
    const cells = rows[0]!.findAll('td').map(td => td.text())
    expect(cells[0]).toContain('WAF 遮断')
    expect(cells[0]).toContain('waf.blocked')
    expect(cells[0]).toContain('AI の根拠')
    expect(cells[1]).toBe('231')
    expect(cells[2]).toBe('180.6')
    expect(cells[3]).toBe('×1.28')
    expect(cells[4]).toBe('Warning')
    expect(cells[5]).toContain('03 時 200 件、17 時 20 件')
    expect(rows[0]!.classes()).toContain('exceeded')
  })
  it('基準なしの行は基準と判定に「基準なし」、比率は —、背景色なし', () => {
    const rows = mountTable().findAll('[data-test="counter-row"]')
    const cells = rows[1]!.findAll('td').map(td => td.text())
    expect(cells[0]).toContain('ALB 4xx')
    expect(cells[0]).not.toContain('AI の根拠')
    expect(cells[2]).toBe('基準なし')
    expect(cells[3]).toBe('—')
    expect(cells[4]).toBe('基準なし')
    expect(cells[5]).toBe('—')
    expect(rows[1]!.classes()).not.toContain('exceeded')
  })
  it('比較の無い行は基準と比率が —、判定は空', () => {
    const cells = mountTable().findAll('[data-test="counter-row"]')[2]!.findAll('td').map(td => td.text())
    expect(cells[0]).toBe('alb.5xx')
    expect(cells[2]).toBe('—')
    expect(cells[3]).toBe('—')
    expect(cells[4]).toBe('')
  })
  it('説明を HelpTip に、24 時間の内訳をツールチップに持たせる', () => {
    const table = mountTable()
    const tips = table.findAllComponents(HelpTip).map(tip => tip.props('text'))
    expect(tips).toContain('Web ACL（backoffice 等）が遮断したリクエスト')
    const breakdown = table.get('[data-test="counter-hourly"]')
    expect(breakdown.text()).toBe('03 時 200 件、17 時 20 件')
    const tooltips = table.findAllComponents({ name: 'VTooltip' }).map(tooltip => String(tooltip.props('text')))
    expect(tooltips.some(text => text.startsWith('00 時 1 件 / ') && text.includes('03 時 200 件') && text.endsWith('23 時 1 件'))).toBe(true)
  })
  it('glossary・comparisons・hourly の無い旧記録ではキー名だけを 1 回出し、落ちない', () => {
    const table = mountTable({ comparisons: undefined, hourly: undefined, glossary: undefined, aiKeys: undefined, counters: { 'waf.blocked': 231 } })
    const row = table.get('[data-test="counter-row"]')
    const cells = row.findAll('td').map(td => td.text())
    expect(cells[0]).toBe('waf.blocked')
    expect(cells[1]).toBe('231')
    expect(cells[2]).toBe('—')
    expect(cells[4]).toBe('')
    expect(cells[5]).toBe('—')
    expect(table.findAllComponents(HelpTip).some(tip => tip.props('text') === '')).toBe(false)
  })
  it.each([
    [1073741824, 536870912, '', '1.0 GB', '512.0 MB'],
    [0, 0, '', '0 B', '0 B'],
    [1024 ** 5, 1024 ** 4, '', '1,024.0 TB', '1.0 TB'],
    [1024, null, '', '1.0 KB', '—'],
    [1024, null, '基準なし', '1.0 KB', '基準なし'],
  ] as const)('S3 ダウンロード量 %s と基準 %s をバイト単位で表示する', (value, baseline, label, expectedValue, expectedBaseline) => {
    const table = mountTable({
      counters: { 's3.bytes_downloaded': value, 's3.get_requests': 1200 },
      comparisons: {
        's3.bytes_downloaded': { baseline, days: 7, exceeded: false, ratio: null, label },
        's3.get_requests': { baseline: 1000.5, days: 7, exceeded: false, ratio: 1.2, label: '' },
      },
    })
    const rows = table.findAll('[data-test="counter-row"]')
    expect(rows[0]!.findAll('td').slice(1, 3).map(td => td.text())).toEqual([expectedValue, expectedBaseline])
    expect(rows[1]!.findAll('td').slice(1, 3).map(td => td.text())).toEqual(['1,200', '1,000.5'])
  })
  it('S3 ダウンロード量に比較がない旧記録でも値をバイト単位で表示する', () => {
    const table = mountTable({ counters: { 's3.bytes_downloaded': 1024 }, comparisons: undefined })
    expect(table.get('[data-test="counter-row"]').findAll('td').slice(1, 3).map(td => td.text())).toEqual(['1.0 KB', '—'])
  })
  it('集計値が無ければ表を出さない', () => {
    const table = mountTable({ counters: {} })
    expect(table.find('table').exists()).toBe(false)
    expect(table.text()).toContain('集計値はありません')
  })
})

describe('CounterTable の「ログを見る」', () => {
  it('logs のときだけ CloudWatch 指標の行にボタンを出し、押すと metric の参照を知らせる', async () => {
    const table = mountTable({ counters: { 'waf.blocked': 231, 'alarmhuba.admin_external': 12 }, logs: true })
    expect(table.findAll('thead th').map(th => th.text())).toContain('ログ')
    const rows = table.findAll('[data-test="counter-row"]')
    expect(rows[0]!.find('[data-test="log-button"]').exists()).toBe(true)
    expect(rows[1]!.find('[data-test="log-button"]').exists()).toBe(false)
    await rows[0]!.get('[data-test="log-button"]').trigger('click')
    expect(table.emitted('log')).toEqual([[{ kind: 'metric', key: 'waf.blocked' }]])
  })
  it('logs でなければボタンも列も出さない', () => {
    const table = mountTable()
    expect(table.find('[data-test="log-button"]').exists()).toBe(false)
    expect(table.findAll('thead th').map(th => th.text())).not.toContain('ログ')
  })
  it('指標の行が無ければ logs でも列を出さない', () => {
    const table = mountTable({ counters: { 'alarmhuba.admin_external': 12 }, logs: true })
    expect(table.findAll('thead th').map(th => th.text())).not.toContain('ログ')
  })
})
