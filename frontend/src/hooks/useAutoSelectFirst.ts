import { useEffect } from 'react';

/**
 * 목록 로드 후 유효한 선택 보장 — 워크스페이스/프로젝트 자동 선택 공용.
 * 목록이 비면 선택 해제, 선택이 목록에 없으면 첫 항목 선택.
 */
export function useAutoSelectFirst(
  items: Array<{ id: string }> | undefined,
  selectedId: string | null,
  setSelected: (id: string | null) => void
) {
  useEffect(() => {
    if (!items) {
      return;
    }

    if (items.length === 0) {
      setSelected(null);
      return;
    }

    const hasSelected = selectedId
      ? items.some((item) => item.id === selectedId)
      : false;

    if (!hasSelected) {
      setSelected(items[0].id);
    }
  }, [items, selectedId, setSelected]);
}
