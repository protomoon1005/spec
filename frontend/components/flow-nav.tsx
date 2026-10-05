"use client";

// 흐름 화면 머리줄. 지금 주소가 흐름의 몇 번째 단계인지 레일에 표시한다.
// 단계 이동은 링크일 뿐이다 — 각 화면이 필요한 값(전략서 번호 등)은 그 화면이 탭 저장소에서
// 직접 읽고, 없으면 스스로 안내한다.

import Link from "next/link";
import { usePathname } from "next/navigation";

const STEPS = [
  { href: "/login", label: "로그인", match: ["/login", "/signup"] },
  { href: "/survey", label: "투자 성향", match: ["/survey", "/profile", "/bounds"] },
  { href: "/compile", label: "전략서 만들기", match: ["/compile"] },
  { href: "/hardcap", label: "하드캡", match: ["/hardcap"] },
  { href: "/backtest", label: "백테스트", match: ["/backtest"] },
  { href: "/confirm", label: "최종 확인", match: ["/confirm"] },
  { href: "/views", label: "관점 브리핑", match: ["/views"] },
];

const LINKS = [
  { href: "/home", label: "홈" },
  { href: "/specs", label: "전략 관리" },
  { href: "/report", label: "리포트" },
];

const hit = (path: string, prefix: string) => path === prefix || path.startsWith(`${prefix}/`);

export default function FlowNav({ wide = false }: { wide?: boolean }) {
  const path = usePathname() ?? "";
  const current = STEPS.findIndex((s) => s.match.some((m) => hit(path, m)));

  return (
    <header className="flow-head">
      <div className={wide ? "flow-head-inner wide" : "flow-head-inner"}>
        <div className="flow-brand-row">
          <Link href="/" className="flow-brand">
            <span className="flow-dot" aria-hidden />
            spec
            <small>국내 ETF 모의운용</small>
          </Link>
          <nav className="flow-links" aria-label="바로가기">
            {LINKS.map((l) => (
              <Link key={l.href} href={l.href} aria-current={hit(path, l.href) ? "page" : undefined}>
                {l.label}
              </Link>
            ))}
          </nav>
        </div>
        <nav aria-label="진행 단계">
          <ol className="flow-rail">
            {STEPS.map((s, i) => (
              <li key={s.href}>
                <Link
                  href={s.href}
                  aria-current={i === current ? "step" : undefined}
                  className={current >= 0 && i < current ? "passed" : undefined}
                >
                  <span className="num">{i + 1}</span>
                  {s.label}
                </Link>
              </li>
            ))}
          </ol>
        </nav>
      </div>
    </header>
  );
}
