import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// vitest は frontend 直下で走る
function read(path: string): string {
  return readFileSync(path, 'utf-8')
}

/** Vuetify 3 の $typography の値。Vuetify 4 では消えたので互換定義で補っている。 */
const SCALE: readonly [string, string, string][] = [
  ['text-h1', '6rem', '300'],
  ['text-h2', '3.75rem', '300'],
  ['text-h3', '3rem', '400'],
  ['text-h4', '2.125rem', '400'],
  ['text-h5', '1.5rem', '400'],
  ['text-h6', '1.25rem', '500'],
  ['text-subtitle-1', '1rem', '400'],
  ['text-subtitle-2', '0.875rem', '500'],
  ['text-body-1', '1rem', '400'],
  ['text-body-2', '0.875rem', '400'],
  ['text-caption', '0.75rem', '400'],
  ['text-overline', '0.75rem', '500'],
]

function ruleOf(css: string, className: string): string {
  const match = new RegExp(`\\.${className}\\s*\\{([^}]*)\\}`).exec(css)
  expect(match, `${className} の定義が無い`).not.toBeNull()
  return match![1]
}

describe('旧タイポグラフィクラスの互換定義', () => {
  const css = read('src/styles/typography.scss')

  it.each(SCALE)('%s が Vuetify 3 の大きさ・太さで定義される', (className, size, weight) => {
    const rule = ruleOf(css, className)

    expect(rule).toContain(`font-size: ${size} !important`)
    expect(rule).toContain(`font-weight: ${weight} !important`)
    expect(rule).toMatch(/line-height:\s*[^;]+!important/)
    expect(rule).toMatch(/letter-spacing:\s*[^;]+!important/)
  })

  it('overline だけ大文字にする', () => {
    expect(ruleOf(css, 'text-overline')).toContain('text-transform: uppercase !important')
    expect(ruleOf(css, 'text-h5')).toContain('text-transform: none !important')
  })

  // 読み込み忘れると 12 クラスすべてが無言で効かなくなる
  it('main.ts から読み込まれる', () => {
    expect(read('src/main.ts')).toContain("import './styles/typography.scss'")
  })

  // 中華フォントへのフォールバックを避けるため、フォント指定は .v-application に任せる
  it('font-family を上書きしない', () => {
    expect(css).not.toMatch(/font-family\s*:/)
  })
})
