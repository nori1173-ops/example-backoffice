import type { Router } from 'vue-router'
import { SITE_TITLE, siteEnv } from '@/config/site'
import { useAuthStore } from '@/stores/auth'

/** 検証サイトのタブをタイトルの先頭で見分けられるようにする（UI-12） */
const VERIFICATION_PREFIX = '[検証] '

/** 認証済みユーザーが開く必要のないルート（設計 9.6） */
const GUEST_ONLY_ROUTE_NAMES: readonly string[] = ['login', 'signup', 'forgot-password']

export function installGuards(router: Router): void {
  router.beforeEach(async (to) => {
    const auth = useAuthStore()
    if (!auth.initialized) {
      await auth.checkAuth()
    }

    const requiresAuth = to.matched.every((record) => record.meta.requiresAuth !== false)
    if (requiresAuth && !auth.isAuthenticated) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }

    if (
      auth.isAuthenticated &&
      typeof to.name === 'string' &&
      GUEST_ONLY_ROUTE_NAMES.includes(to.name)
    ) {
      return { name: 'home' }
    }

    const requiredGroups = to.matched.flatMap((record) => record.meta.requiredGroups ?? [])
    const allowed = requiredGroups.some((group) => auth.groups.includes(group))
    if (requiredGroups.length > 0 && !allowed) {
      return { name: 'forbidden' }
    }

    return true
  })

  router.afterEach((to) => {
    const title = [to.meta.title, SITE_TITLE].filter(Boolean).join(' | ')
    document.title = siteEnv() === 'dev' ? `${VERIFICATION_PREFIX}${title}` : title
  })
}
