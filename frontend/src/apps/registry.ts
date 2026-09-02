import type { AppManifest } from './types'

const DEFAULT_ORDER = 50

export interface AppRegistry {
  apps: AppManifest[]
  findApp: (id: string) => AppManifest | undefined
}

export function createRegistry(manifests: AppManifest[]): AppRegistry {
  const sorted = [...manifests].sort(
    (a, b) =>
      (a.order ?? DEFAULT_ORDER) - (b.order ?? DEFAULT_ORDER) ||
      a.title.localeCompare(b.title, 'ja'),
  )
  return {
    apps: sorted,
    findApp: (id) => sorted.find((app) => app.id === id),
  }
}

const modules = import.meta.glob<{ default: AppManifest }>(['./*/index.ts', '!./_*/index.ts'], {
  eager: true,
})

const registry = createRegistry(Object.values(modules).map((module) => module.default))

export const apps: AppManifest[] = registry.apps

export function findApp(id: string): AppManifest | undefined {
  return registry.findApp(id)
}
