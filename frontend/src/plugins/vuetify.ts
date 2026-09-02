import { createVuetify } from 'vuetify'
import { en, ja } from 'vuetify/locale'
import 'vuetify/styles'

export default createVuetify({
  locale: { locale: 'ja', fallback: 'en', messages: { ja, en } },
  theme: { defaultTheme: 'light' },
  defaults: {
    VTextField: { density: 'compact', variant: 'outlined', hideDetails: 'auto' },
    VSelect: { density: 'compact', variant: 'outlined', hideDetails: 'auto' },
    VAutocomplete: { density: 'compact', variant: 'outlined', hideDetails: 'auto' },
  },
})
