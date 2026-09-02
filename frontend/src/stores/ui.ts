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
  const snackbar = ref<SnackbarState>({ ...HIDDEN_SNACKBAR })

  function toggleDrawer(): void {
    drawer.value = !drawer.value
  }

  function toggleRail(): void {
    rail.value = !rail.value
  }

  function notify(text: string, color: SnackbarColor = 'info'): void {
    snackbar.value = { visible: true, text, color }
  }

  return { drawer, rail, snackbar, toggleDrawer, toggleRail, notify }
})
