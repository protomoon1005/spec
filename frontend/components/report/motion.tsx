"use client";

import { useEffect, useLayoutEffect, useState } from "react";

// 서버에서 정적 프리렌더되므로 첫 렌더는 반드시 최종값이어야 한다. 0 으로
// 시작하면 서버 HTML 과 클라이언트 첫 렌더가 달라져 하이드레이션이 어긋난다.
// 그래서 최종값으로 렌더한 뒤, 브라우저가 그리기 전(useLayoutEffect)에 0 으로
// 되돌리고 애니메이션을 시작한다 — 최종값이 한 프레임 번쩍이지 않는다.
export const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

const COUNT_UP_MS = 1000;

// easeOutExpo. 초반에 빠르게 올라갔다가 끝에서 부드럽게 멎는다.
const easeOutExpo = (t: number) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));

export function useCountUp(target: number, delay = 0, duration = COUNT_UP_MS): number {
  const [value, setValue] = useState(target);

  useIsomorphicLayoutEffect(() => {
    // 동작 최소화를 켠 사용자에게는 애니메이션을 걸지 않는다.
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setValue(target);
      return;
    }

    let raf = 0;
    let startedAt = 0;
    setValue(0);

    const tick = (now: number) => {
      if (startedAt === 0) startedAt = now;
      const elapsed = now - startedAt - delay;
      if (elapsed < 0) {
        raf = requestAnimationFrame(tick);
        return;
      }
      const t = Math.min(1, elapsed / duration);
      setValue(target * easeOutExpo(t));
      if (t < 1) raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, delay, duration]);

  return value;
}

// 동작 최소화 설정을 읽는다. 첫 렌더는 false 로 두고(서버에는 matchMedia 가 없다)
// 그리기 전에 실제 값으로 맞춘다 — 하이드레이션이 어긋나지 않는다.
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useIsomorphicLayoutEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

// 차트가 그려지는 시간. 선은 왼쪽에서 오른쪽으로 훑고, 막대는 바닥에서 자란다.
export const CHART_DRAW_MS = 1100;
export const BAR_DRAW_MS = 800;

export function CountUp({
  value,
  format,
  delay = 0,
}: {
  value: number;
  format: (n: number) => string;
  delay?: number;
}) {
  const shown = useCountUp(value, delay);
  return <>{format(shown)}</>;
}



