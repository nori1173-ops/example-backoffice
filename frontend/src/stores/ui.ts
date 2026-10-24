import { defineStore } from 'pinia'
import { ref } from 'vue'

export type SnackbarColor = 'success' | 'info' | 'warning' | 'error'

export interface SnackbarState {
  visible: boolean
  text: string
  color: SnackbarColor
}

const HIDDEN_SNACKBAR: SnackbarState = { visible: false, text: '', color: 'info' }

export const useUiStore = defineStore('ui', () => {
  const drawer = ref(true)
  const rail = ref(false)
  // 説明ツールチップの一括切替。覚えさせると常時 ON のまま忘れられるので保存しない（#1012）
  const helpMode = ref(false)
  const snackbar = ref<SnackbarState>({ ...HIDDEN_SNACKBAR })

  function toggleDrawer(): void {
    drawer.value = !drawer.value
  }

  function toggleRail(): void {
    rail.value = !rail.value
  }

  function toggleHelpMode(): void {
    helpMode.value = !helpMode.value
  }

  function notify(text: string, color: SnackbarColor = 'info'): void {
    snackbar.value = { visible: true, text, color }
  }

  return { drawer, rail, helpMode, snackbar, toggleDrawer, toggleRail, toggleHelpMode, notify }
})
