import { describe, it, expect } from 'vitest';
import { getMonday } from './weekUtils';

describe('getMonday', () => {
  it('수요일 → 같은 주 월요일', () => {
    // 2026-07-01 은 수요일
    const monday = getMonday(new Date(2026, 6, 1, 15, 30));
    expect(monday.getFullYear()).toBe(2026);
    expect(monday.getMonth()).toBe(5);
    expect(monday.getDate()).toBe(29);
  });

  it('월요일 → 그날 자정', () => {
    const monday = getMonday(new Date(2026, 5, 29, 23, 59));
    expect(monday.getDate()).toBe(29);
    expect(monday.getHours()).toBe(0);
    expect(monday.getMinutes()).toBe(0);
  });

  it('일요일 → 지난 월요일 (다음 주 아님)', () => {
    // 2026-07-05 는 일요일
    const monday = getMonday(new Date(2026, 6, 5));
    expect(monday.getMonth()).toBe(5);
    expect(monday.getDate()).toBe(29);
  });

  it('원본 Date 를 변경하지 않는다', () => {
    const input = new Date(2026, 6, 1, 15, 30);
    getMonday(input);
    expect(input.getDate()).toBe(1);
    expect(input.getHours()).toBe(15);
  });
});
