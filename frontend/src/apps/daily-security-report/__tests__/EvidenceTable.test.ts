import { afterEach, describe, expect, it } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import EvidenceTable from '../components/EvidenceTable.vue'
import type { EvidenceRow } from '../types'

let wrapper: VueWrapper
function mountTable(rows: (EvidenceRow | string)[]) {
  wrapper = mount(EvidenceTable, { props: { rows, label: '試験' }, global: { plugins: [createVuetify({ components, directives })] } })
  return wrapper
}
afterEach(() => wrapper?.unmount())

describe('EvidenceTable', () => {
  it('AWS コンソールの link だけを新しいタブのリンクにする', () => {
    const console = 'https://ap-northeast-1.console.aws.amazon.com/guardduty/home?region=ap-northeast-1#/findings?macros=current&fId=f1'
    const table = mountTable([
      { title: 'Recon:EC2/PortProbe', link: console },
      { title: 'other', link: 'https://example.com/?u=console.aws.amazon.com' },
      { title: 'none', link: null },
    ])
    expect(table.findAll('thead th').map(th => th.text())).toEqual(['内容', 'コンソール'])
    const rows = table.findAll('tbody tr')
    const anchor = rows[0]!.get('a')
    expect(anchor.text()).toBe('コンソール')
    expect(anchor.attributes('href')).toBe(console)
    expect(anchor.attributes('target')).toBe('_blank')
    expect(anchor.attributes('rel')).toBe('noopener')
    expect(rows[1]!.find('a').exists()).toBe(false)
    expect(rows[1]!.text()).toContain('https://example.com/?u=console.aws.amazon.com')
    expect(rows[2]!.findAll('td')[1]!.text()).toBe('—')
  })
  it('追跡の列を日本語の見出しにし、時刻と時間帯を整形し、ref は出さない', () => {
    const hourly = Array.from({ length: 24 }, (_, hour) => (hour === 17 ? 7 : 0))
    const table = mountTable([{
      function: 'example-fn', count: 7, hourly, where: 'CloudWatch Logs /aws/lambda/example-fn', ref: { kind: 'lambda', function: 'example-fn' },
      event_id: 'evt-1', first_seen: '2026-10-14T09:12:00+09:00', last_seen: '2026-10-14T21:40:00+09:00', resource: 'i-example', remote_ip: '203.0.113.9', id: 'f1', region: 'ap-northeast-1',
    }])
    const heads = table.findAll('thead th').map(th => th.text())
    expect(heads).toEqual(['関数', '件数', '時間帯', '場所', 'イベント ID', '初回観測', '最終観測', '対象', '相手 IP', 'ID', 'リージョン'])
    const cells = table.findAll('tbody td').map(td => td.text())
    expect(cells).toEqual(['example-fn', '7', '17 時 7 件', 'CloudWatch Logs /aws/lambda/example-fn', 'evt-1', '2026-10-14 09:12', '2026-10-14 21:40', 'i-example', '203.0.113.9', 'f1', 'ap-northeast-1'])
    expect(table.text()).not.toContain('kind')
  })
  it('時間帯が全部 0 なら —、文字列行は名前の列', () => {
    const table = mountTable([{ function: 'f', hourly: new Array(24).fill(0) }])
    expect(table.findAll('tbody td')[1]!.text()).toBe('—')
    expect(mountTable(['user-example']).findAll('tbody td')[0]!.text()).toBe('user-example')
  })
  it('日付だけの観測日はそのまま出す', () => {
    const table = mountTable([{ actor: 'user-example', last_seen: '2026-09-01' }])
    expect(table.findAll('tbody td')[1]!.text()).toBe('2026-09-01')
  })
})

describe('EvidenceTable の「ログを見る」', () => {
  const lambdaRef = { kind: 'lambda', function: 'example-fn' }
  const console = 'https://ap-northeast-1.console.aws.amazon.com/cloudwatch/home?region=ap-northeast-1'
  function mountLogs(rows: (EvidenceRow | string)[], logs = true) {
    wrapper = mount(EvidenceTable, { props: { rows, label: '試験', logs }, global: { plugins: [createVuetify({ components, directives })] } })
    return wrapper
  }
  it('ref のある行だけにボタンを出し、押すと ref とリンクを知らせる', async () => {
    const table = mountLogs([
      { function: 'example-fn', count: 7, ref: lambdaRef, link: console },
      { function: 'other-fn', count: 1 },
      { function: 'broken-fn', count: 1, ref: { kind: 'lambda' } },
    ])
    expect(table.findAll('thead th').map(th => th.text())).toEqual(['関数', '件数', 'コンソール', 'ログ'])
    const rows = table.findAll('tbody tr')
    expect(rows.map(row => row.find('[data-test="log-button"]').exists())).toEqual([true, false, false])
    await rows[0]!.get('[data-test="log-button"]').trigger('click')
    expect(table.emitted('log')).toEqual([[lambdaRef, console]])
  })
  it('logs でなければ ref があってもボタンも列も出さない', () => {
    const table = mountLogs([{ function: 'example-fn', count: 7, ref: lambdaRef }], false)
    expect(table.find('[data-test="log-button"]').exists()).toBe(false)
    expect(table.findAll('thead th').map(th => th.text())).toEqual(['関数', '件数'])
  })
  it('ref のある行が無ければ logs でも列を出さない', () => {
    const table = mountLogs([{ function: 'other-fn', count: 1 }, 'plain-name'])
    expect(table.findAll('thead th').map(th => th.text())).toEqual(['関数', '件数', '名前'])
  })
})
