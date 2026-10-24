import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useUiStore } from './ui'

describe('useUiStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  // ヘルプモードはセッション内だけの表示切替で、保存も共有もしない（#1012）
  it('ヘルプモードは OFF で始まる', () => {
    expect(useUiStore().helpMode).toBe(false)
  })

  it('toggleHelpMode で ON と OFF が入れ替わる', () => {
    const ui = useUiStore()

    ui.toggleHelpMode()
    expect(ui.helpMode).toBe(true)

    ui.toggleHelpMode()
    expect(ui.helpMode).toBe(false)
  })
})
