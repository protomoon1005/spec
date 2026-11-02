// 전략서 상태 표시. draft 는 아직 고칠 수 있는 상태라 주의색, 승인은 확정색.
export function StatusBadge({ status }: { status: string }) {
  const tone = status === "approved" ? "ok" : status === "draft" ? "warn" : "muted";
  return <span className={`flow-badge ${tone}`}>{status}</span>;
}
