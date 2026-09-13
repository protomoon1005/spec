// 색과 모서리 반경. 값이 여기저기 흩어지면 화면이 따로 놀아서 한 곳에서 관리한다.
export const C = {
  bg: "#0b0f14",
  surface: "#131920",
  surface2: "#1a2330",
  border: "#1e2d3d",
  grid: "#22344a",
  text: "#c9d6e3",
  bright: "#e2ecf5",
  muted: "#7f9ab5",
  dim: "#4d6a84",
  profit: "#00e676",
  loss: "#ff5c7a",
  accent: "#3d9bff",
  warn: "#ffb020",
};

// 모서리 반경. 값이 여기저기 흩어지면 화면이 따로 놀아서 한 곳에서 관리한다.
export const R = {
  panel: 14,
  card: 12,
  inner: 10,
  pill: 999,
  button: 10,
  bar: 4,
  band: 8,
};

export const MONO = "var(--font-mono)";
export const SANS = "var(--font-sans)";

export const sign = (n: number) => (n >= 0 ? C.profit : C.loss);

