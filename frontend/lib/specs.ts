// 내 전략서 목록 — GET /specs. 전략 관리와 전략 삭제 화면이 같이 쓴다.

export type SpecListItem = {
  spec_id: string;
  name: string;
  status: string;
  created_at: string;
  universe_size: number;
};

export const formatDate = (iso: string) => new Date(iso).toLocaleString("ko-KR");
