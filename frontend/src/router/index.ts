import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { apps } from '@/apps/registry'
import { installGuards } from './guards'

const appRoutes: RouteRecordRaw[] = apps.map((app) => ({
  path: `/${app.id}`,
  meta: { appId: app.id, requiredGroups: app.requiredGroups },
  children: app.routes,
}))

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { requiresAuth: false, layout: 'auth', title: 'ログイン' },
  },
  {
    path: '/signup',
    name: 'signup',
    component: () => import('@/views/SignupView.vue'),
    meta: { requiresAuth: false, layout: 'auth', title: 'サインアップ' },
  },
  {
    path: '/',
    component: () => import('@/layouts/ShellLayout.vue'),
    meta: { layout: 'shell' },
    children: [
      {
        path: '',
        name: 'home',
        component: () => import('@/views/HomeView.vue'),
        meta: { title: 'ホーム' },
      },
      {
        path: 'mfa',
        name: 'mfa',
        component: () => import('@/views/MfaSetupView.vue'),
        meta: { title: 'セキュリティ' },
      },
      {
        path: 'forbidden',
        name: 'forbidden',
        component: () => import('@/views/ForbiddenView.vue'),
        meta: { title: 'アクセス権限がありません' },
      },
      ...appRoutes,
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFoundView.vue'),
    meta: { title: 'ページが見つかりません' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

installGuards(router)

export default router
