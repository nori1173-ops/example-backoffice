import { vi } from 'vitest'

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
if (!('ResizeObserver' in globalThis)) {
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver
}

// Vuetify のオーバーレイは visualViewport を素の識別子で参照するため、jsdom では宣言が要る
if (!('visualViewport' in window)) {
  Object.defineProperty(window, 'visualViewport', { value: null, writable: true })
}

const cryptoStub = {
  subtle: globalThis.crypto?.subtle,
  getRandomValues: (arr: Uint8Array) => {
    for (let i = 0; i < arr.length; i++) {
      arr[i] = Math.floor(Math.random() * 256)
    }
    return arr
  },
  randomUUID: () =>
    'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = Math.floor(Math.random() * 16)
      return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
    }),
}
if (
  typeof globalThis.crypto?.getRandomValues !== 'function' ||
  typeof globalThis.crypto?.randomUUID !== 'function'
) {
  Object.defineProperty(globalThis, 'crypto', { value: cryptoStub, writable: true })
}

vi.mock('aws-amplify/auth', () => ({
  signIn: vi.fn(),
  signOut: vi.fn(),
  signUp: vi.fn(),
  confirmSignUp: vi.fn(),
  getCurrentUser: vi.fn(),
  fetchAuthSession: vi.fn(),
  confirmSignIn: vi.fn(),
  resendSignUpCode: vi.fn(),
  resetPassword: vi.fn(),
  confirmResetPassword: vi.fn(),
  setUpTOTP: vi.fn(),
  verifyTOTPSetup: vi.fn(),
  updateMFAPreference: vi.fn(),
  fetchMFAPreference: vi.fn(),
}))
