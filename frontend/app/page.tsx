"use client";

// 시작. 로그인 화면이 없으므로 바로 채팅 화면으로 보낸다.

import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function HomePage() {
  const router = useRouter();
  useEffect(() => router.replace("/chat"), [router]);
  return null;
}
