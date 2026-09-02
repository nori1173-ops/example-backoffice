import '@mdi/font/css/materialdesignicons.css'
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import vuetify from './plugins/vuetify'
import router from './router'
import { configureAmplify } from './config/amplify'

configureAmplify()

const app = createApp(App)
app.use(createPinia())
app.use(vuetify)
app.use(router)
app.mount('#app')
