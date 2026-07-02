import { useEffect, useRef, useState } from 'react';

/**
 * 모바일 사이드바 열림 상태 — 데스크톱 전환 시 자동 닫힘 + 좌측 엣지 스와이프 열기.
 */
export function useMobileSidebar() {
  const [isOpen, setOpen] = useState(false);
  const swipeStartXRef = useRef<number | null>(null);
  const swipeStartYRef = useRef<number | null>(null);
  const isSwipeTrackingRef = useRef(false);

  useEffect(() => {
    const mediaQuery = window.matchMedia('(min-width: 768px)');
    const handleChange = (event: MediaQueryListEvent) => {
      if (event.matches) {
        setOpen(false);
      }
    };

    mediaQuery.addEventListener('change', handleChange);

    return () => {
      mediaQuery.removeEventListener('change', handleChange);
    };
  }, []);

  useEffect(() => {
    const EDGE_START_PX = 64;
    const OPEN_THRESHOLD_PX = 72;
    const MAX_VERTICAL_DRIFT_PX = 64;

    const resetSwipe = () => {
      swipeStartXRef.current = null;
      swipeStartYRef.current = null;
      isSwipeTrackingRef.current = false;
    };

    const handleTouchStart = (event: TouchEvent) => {
      if (window.matchMedia('(min-width: 768px)').matches || isOpen) {
        return;
      }

      const touch = event.touches[0];
      if (!touch || touch.clientX > EDGE_START_PX) {
        resetSwipe();
        return;
      }

      swipeStartXRef.current = touch.clientX;
      swipeStartYRef.current = touch.clientY;
      isSwipeTrackingRef.current = true;
    };

    const handleTouchEnd = (event: TouchEvent) => {
      if (!isSwipeTrackingRef.current) {
        return;
      }

      const startX = swipeStartXRef.current;
      const startY = swipeStartYRef.current;
      const touch = event.changedTouches[0];

      if (startX === null || startY === null || !touch) {
        resetSwipe();
        return;
      }

      const deltaX = touch.clientX - startX;
      const deltaY = Math.abs(touch.clientY - startY);

      if (deltaX >= OPEN_THRESHOLD_PX && deltaY <= MAX_VERTICAL_DRIFT_PX) {
        setOpen(true);
      }

      resetSwipe();
    };

    window.addEventListener('touchstart', handleTouchStart, { passive: true });
    window.addEventListener('touchend', handleTouchEnd, { passive: true });
    window.addEventListener('touchcancel', resetSwipe, { passive: true });

    return () => {
      window.removeEventListener('touchstart', handleTouchStart);
      window.removeEventListener('touchend', handleTouchEnd);
      window.removeEventListener('touchcancel', resetSwipe);
    };
  }, [isOpen]);

  const close = () => {
    setOpen(false);
  };

  const toggle = () => {
    setOpen((prev) => !prev);
  };

  return { isOpen, close, toggle };
}
