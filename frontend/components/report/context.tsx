"use client";

import { createContext, useContext } from "react";
import { defaultReport, type Report } from "@/lib/data";

// 리포트 본문과 그 아래 조각(툴팁 · 지표 해설)이 같은 결과를 보게 한다.
// 예전에는 조각들이 lib/data 의 모듈 상수를 직접 import 해서, 본문이 다른 결과를
// 그려도 툴팁과 해설은 늘 정적 데모 결과를 보여 줄 수 있었다.
// 기본값은 정적 데모 결과라 Provider 밖에서도 깨지지 않는다.
const ReportContext = createContext<Report>(defaultReport);

export const ReportProvider = ReportContext.Provider;

export function useReport(): Report {
  return useContext(ReportContext);
}
