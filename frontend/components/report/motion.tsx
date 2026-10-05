"use client";

import { useEffect, useLayoutEffect, useState } from "react";

// 서버에서 정적 프리렌더되므로 첫 렌더는 반드시 최종값이어야 한다. 0 으로
// 시작하면 서버 HTML 과 클라이언트 첫 렌더가 달라져 하이드레이션이 어긋나고,
// JS 가 꺼진 환경에서는 0 만 남는다. 그래서 최종값으로 렌더한 뒤 애니메이션을
// 시작한다.
//
// 되돌리는 시점이 중요하다. 예전에는 useLayoutEffect 안에서 곧바로 0 으로
// 되돌렸는데, 그러면 rAF 가 오지 않는 경로(숨은 탭 · 백그라운드 iframe ·
// 스크린샷/PDF 캡처)에서 0 만 남고 영영 올라오지 않는다. 지금은 첫 프레임이
// 실제로 돈 것을 확인한 뒤에만 0 으로 내린다.
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

    // 숨은 탭에서는 rAF 가 아예 돌지 않는다. 애니메이션을 걸지 않고 최종값을 둔다.
    if (document.hidden) {
      setValue(target);
      return;
    }

    let raf = 0;
    let startedAt = 0;
    let settle = 0;

    const tick = (now: number) => {
      const elapsed = now - startedAt - delay;
      if (elapsed < 0) {
        raf = requestAnimationFrame(tick);
        return;
      }
      const t = Math.min(1, elapsed / duration);
      setValue(target * easeOutExpo(t));
      if (t < 1) raf = requestAnimationFrame(tick);
    };

    // 0 으로 되돌리는 것은 첫 프레임이 실제로 돈 뒤다. 여기서 되돌려 놓고
    // rAF 가 오지 않으면(백그라운드 iframe, 스크린샷·PDF 캡처 경로) 숫자가
    // 0 에 영영 멈춘다 — 승인 화면이 ₩0, 0.0% 로 뜬다.
    raf = requestAnimationFrame((now) => {
      setValue(0);
      startedAt = now;
      raf = requestAnimationFrame(tick);
      // 도중에 프레임이 멎어도 최종값은 반드시 남긴다.
      settle = window.setTimeout(() => setValue(target), delay + duration + 200);
    });

    return () => {
      cancelAnimationFrame(raf);
      clearTimeout(settle);
    };
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

// 차트 애니메이션이 끝났다고 보는 시점. 가장 늦은 것(시작 지연 240ms + 1100ms)에
// 여유를 더한다.
export const CHART_SETTLE_MS = CHART_DRAW_MS + 600;

// key 가 바뀐 뒤 ms 가 지나면 true. 차트 애니메이션의 안전망이다.
//
// recharts 는 차트를 requestAnimationFrame 으로 그린다. rAF 는 화면이 그려지지 않을 때
// (창이 다른 창 뒤에 있을 때 · 숨은 탭 · 스크린샷/PDF 캡처) 돌지 않아서, 막대가 첫
// 프레임(높이 0)에 멈춘 채로 남는다. 카운트업이 0 에 멈추던 것과 같은 원인이다.
// setTimeout 은 그때도 돈다. 그래서 이 값이 true 가 되면 애니메이션을 끄고, recharts 가
// rAF 없이 최종 모양을 바로 그리게 한다. 화면이 정상적으로 그려질 때는 이미 애니메이션이
// 끝난 뒤라 끄는 순간 모양이 바뀌지 않는다.
//
// 탭마다 다시 건다(key = 탭). 다른 탭의 차트는 그 탭을 열 때 새로 생기므로, 한 번만 걸면
// 나중에 연 탭의 차트는 그리는 애니메이션 없이 나타난다. 키를 바꾼 그 렌더에서 바로
// false 가 되도록 effect 가 아니라 "어느 키가 끝났는가" 를 비교해 낸다.
export function useSettled(key: string, ms: number): boolean {
  const [settledKey, setSettledKey] = useState<string | null>(null);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettledKey(key), ms);
    return () => window.clearTimeout(timer);
  }, [key, ms]);
  return settledKey === key;
}

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



