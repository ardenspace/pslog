import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(() => {
  cleanup();
});

// jsdom 에는 matchMedia 가 없음 — DashboardPage 의 반응형/사이드바 effect 용 스텁
import { vi } from 'vitest';

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// Node 22+ 의 내장 localStorage 전역이 jsdom 것을 가림 (--localstorage-file 미설정 시 clear 등 미구현)
// → 인메모리 Storage 로 양쪽 전역을 통일
function makeMemoryStorage(): Storage {
  let store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear: () => {
      store = new Map();
    },
    getItem: (key: string) => store.get(key) ?? null,
    key: (index: number) => [...store.keys()][index] ?? null,
    removeItem: (key: string) => {
      store.delete(key);
    },
    setItem: (key: string, value: string) => {
      store.set(key, String(value));
    },
  };
}

const memoryStorage = makeMemoryStorage();
Object.defineProperty(window, 'localStorage', { value: memoryStorage, writable: true });
Object.defineProperty(globalThis, 'localStorage', { value: memoryStorage, writable: true });
