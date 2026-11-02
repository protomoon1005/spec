"""대화형 화면. 그리기만 한다 — 상태 처리는 ui.state, 판정·검사·백테스트는 core.pipeline.run.

실행: streamlit run app.py
"""
from __future__ import annotations

import html

import plotly.graph_objects as go
import streamlit as st

from core.decide import DIRECT
from core.slot_fill import MODEL, ollama_status
from ui import state as S

st.set_page_config(page_title="전략 시험실", page_icon="📈", layout="centered", initial_sidebar_state="expanded")

ACCENT = "#2a78d6"
SERIES = {"strategy": ("전략", ACCENT), "kospi": ("코스피", "#eb6834"), "buy_hold": ("단순 보유", "#1baf7a")}
INK, MUTED = "#1f2328", "#6b7280"

st.markdown(f"""<style>
.block-container {{ max-width: 760px; padding-top: 2.5rem; padding-bottom: 6rem; }}
.u-row {{ display: flex; justify-content: flex-end; margin: .75rem 0; }}
.u-bubble {{ background: #eef1f5; border-radius: 18px; padding: .6rem 1rem; max-width: 80%; white-space: pre-wrap; }}
.hero {{ text-align: center; font-size: 2rem; font-weight: 600; margin: 3.5rem 0 1.5rem; }}
.tag {{ font-size: .75rem; color: {MUTED}; background: #f1f2f4; border-radius: 6px; padding: 1px 6px; margin-left: 2px; }}
mark {{ background: #fde68a; border-radius: 4px; padding: 0 2px; }}
.muted {{ color: {MUTED}; font-size: .85rem; }}
</style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- 세션

@st.cache_data(ttl=15, show_spinner=False)
def _ollama() -> tuple[bool, str]:
    return ollama_status()


def _conv() -> dict:
    if "conv" not in st.session_state:
        st.session_state.conv = S.new_conversation()
    return st.session_state.conv


def _act(**kw) -> None:
    st.session_state.action = kw


def _open(conv_id: str) -> None:
    if (c := S.load(conv_id)) is not None:
        st.session_state.conv = c


def _new() -> None:
    st.session_state.conv = S.new_conversation()


# ---------------------------------------------------------------- 사이드바

def sidebar(conv: dict) -> None:
    with st.sidebar:
        st.button("＋ 새 전략", on_click=_new, width="stretch", type="primary")
        st.caption("지난 대화")
        items = S.list_history()
        if not items:
            st.markdown('<span class="muted">아직 없습니다.</span>', unsafe_allow_html=True)
        for it in items:
            st.button(it["title"], key=f"h-{it['id']}", on_click=_open, args=(it["id"],), width="stretch",
                      type="secondary" if it["id"] == conv["id"] else "tertiary")
        st.divider()
        ok, msg = _ollama()
        dot = "🟢" if ok else "🔴"
        st.markdown(f"{dot} Ollama {'연결됨' if ok else '꺼짐'} · `{MODEL}`", help=msg)


# ---------------------------------------------------------------- 메시지

def user_bubble(text: str) -> None:
    st.markdown(f'<div class="u-row"><div class="u-bubble">{html.escape(text)}</div></div>', unsafe_allow_html=True)


def draw_clarify(m: dict, idx: int, live: bool) -> None:
    st.markdown(f"**확인할 게 있어요** &nbsp;<span class='tag'>되묻기 {m['round']}/{m['max_rounds']}</span>",
                unsafe_allow_html=True)
    picks, typed = {}, {}
    for q in m["questions"]:
        st.markdown(q["text"])
        if q.get("hint"):
            st.caption(q["hint"])
        k = f"q-{idx}-{q['id']}"
        picks[q["id"]] = st.pills(q["text"], q["options"], key=k, label_visibility="collapsed", disabled=not live)
        if live and picks[q["id"]] == DIRECT:
            typed[q["id"]] = st.text_input("직접 입력", key=k + "-t", placeholder="답을 적어 주세요")
    if live:
        if st.button("답하기", key=f"ans-{idx}", type="primary"):
            reply, err = S.collect_answers(m["questions"], picks, typed)
            if err:
                st.warning(err)
            else:
                _act(type="answer", reply=reply)
                st.rerun()


def draw_reject(m: dict) -> None:
    st.badge(f"{m['code']} · {m['label']}", color="red", icon=":material/block:")
    st.markdown(m["plain"])
    quoted = "".join(f"<mark>{html.escape(t)}</mark>" if hit else html.escape(t) for t, hit in m["segments"])
    st.markdown(f'<div class="muted">원문</div><div>“{quoted}”</div>', unsafe_allow_html=True)
    with st.expander("자세히"):
        st.write(m["detail"])


TAG = '<span class="tag">기본값 사용</span>'


def _part(p: dict) -> str:
    """재서술 조각. 문장 틀에 이미 있는 '(기본값 사용)'은 태그로 바꾸고, 없으면 기본값 조각 끝에 붙인다."""
    t = html.escape(p["text"])
    if "(기본값 사용)" in t:
        return t.replace("(기본값 사용)", TAG)
    return t + (" " + TAG if p["default"] else "")


def draw_understood(m: dict, idx: int) -> None:
    with st.container(border=True):
        st.markdown("**AI가 이해한 전략**")
        body = " ".join(_part(p) for p in m["parts"])
        st.markdown(body, unsafe_allow_html=True)
        if m["defaults"]:
            st.caption("기본값 사용: " + ", ".join(m["defaults"]))
        with st.expander("검사 5단계"):
            icon = {"passed": "✅", "failed": "⛔", "questions": "❓", "warned": "⚠️", "skipped": "➖"}
            for s in m["checklist"]:
                extra = " · ".join(s["issues"]) or s["note"]
                st.markdown(f"{icon[s['status']]} **{s['stage']}단계 {s['name']}** — {s['label']}"
                            + (f"  \n<span class='muted'>{html.escape(extra)}</span>" if extra else ""),
                            unsafe_allow_html=True)
        with st.expander("전략서 JSON"):
            st.json(m["spec"], expanded=True)
        c1, c2, _ = st.columns([1.2, 1, 2])
        used = m.get("used")
        if c1.button("이대로 백테스트", key=f"bt-{idx}", type="primary", disabled=bool(used)):
            _act(type="backtest", idx=idx)
            st.rerun()
        if c2.button("고쳐 말하기", key=f"rv-{idx}", disabled=bool(used)):
            _act(type="revise", idx=idx)
            st.rerun()


def equity_chart(c: dict) -> go.Figure:
    fig = go.Figure()
    for key in ("strategy", "kospi", "buy_hold"):
        if c.get(key) is None:
            continue
        name, color = SERIES[key]
        fig.add_trace(go.Scatter(x=c["dates"], y=c[key], name=name, mode="lines",
                                 line=dict(color=color, width=2 if key == "strategy" else 1.5),
                                 hovertemplate="%{y:,.0f}원"))
    for side, sym, label, fill in (("buys", "triangle-up", "사기 ▲", INK), ("sells", "triangle-down", "팔기 ▼", "#ffffff")):
        pts = c.get(side) or []
        if pts:
            fig.add_trace(go.Scatter(
                x=[p["date"] for p in pts], y=[p["value"] for p in pts], name=label, mode="markers",
                marker=dict(symbol=sym, size=10, color=fill, line=dict(color=INK, width=1.5)),
                customdata=[[p["ticker"], p["price"]] for p in pts],
                hovertemplate="%{customdata[0]} %{customdata[1]:,.0f}원 체결<extra>" + label + "</extra>"))
    fig.update_layout(template="plotly_white", height=360, margin=dict(l=8, r=8, t=8, b=8), hovermode="x unified",
                      legend=dict(orientation="h", y=1.08, x=0, font=dict(color=MUTED)),
                      yaxis=dict(tickformat=",.0f", gridcolor="#eef0f2", ticksuffix="원"),
                      xaxis=dict(gridcolor="#f5f6f7"), font=dict(color=INK))
    return fig


def draw_result(m: dict, idx: int) -> None:
    mt = m["metrics"]
    with st.container(border=True):
        st.markdown(f"**백테스트 결과** <span class='muted'>{mt['start']} ~ {mt['end']}</span>", unsafe_allow_html=True)
        a, b, c, d = st.columns(4)
        a.metric("총수익률", S.fmt_pct(mt["total_return"]))
        b.metric("연 수익률", S.fmt_pct(mt["cagr"]))
        c.metric("MDD", S.fmt_pct(mt["mdd"], signed=False))
        d.metric("거래 횟수", f"{mt['n_trades']}회", help=f"보유 중 {mt['n_open_positions']}건은 빼고 셉니다.")
        st.caption(f"비교 — 코스피 {S.fmt_pct(mt['kospi_total_return'])} · 단순 보유 {S.fmt_pct(mt['buy_hold_total_return'])}")
        st.plotly_chart(equity_chart(m["chart"]), width="stretch", key=f"chart-{idx}",
                        config={"displayModeBar": False})
        for w in m["warnings"]:
            st.warning(w, icon="⚠️")
        with st.expander(f"거래 내역 ({len(m['trades'])}건)"):
            if m["trades"]:
                st.dataframe(m["trades"], hide_index=True, width="stretch")
            else:
                st.write("거래가 없습니다.")
        if m["match"]:
            st.badge(f"다시 실행 → 결과 일치 ✓ {m['hash'][:8]}", color="green")
        else:
            st.badge(f"다시 실행 → 결과 불일치 ✗ {(m['first_hash'] or '—')[:8]} ≠ {m['hash'][:8]}", color="red")
        st.caption(m["costs"] + "  \n" + "  \n".join(m["notes"]))


def draw_messages(conv: dict) -> None:
    last_clarify = max((i for i, m in enumerate(conv["messages"]) if m.get("kind") == "clarify"), default=None)
    for i, m in enumerate(conv["messages"]):
        if m["role"] == "user":
            user_bubble(m["text"])
            continue
        k = m["kind"]
        if k == "clarify":
            draw_clarify(m, i, live=(i == last_clarify and not m.get("done")))
        elif k == "reject":
            draw_reject(m)
        elif k == "understood":
            draw_understood(m, i)
        elif k == "result":
            draw_result(m, i)
        elif k == "error":
            st.error(m["text"])
        else:
            st.markdown(m["text"])
        st.write("")


def empty_screen() -> None:
    ok, msg = _ollama()
    if not ok:
        st.warning(msg + "  \n이미 실행해 본 문장(캐시)은 Ollama 없이도 됩니다.", icon="🔌")
    st.markdown('<div class="hero">어떤 전략을 시험해 볼까요?</div>', unsafe_allow_html=True)
    cols = st.columns(2)
    for i, ex in enumerate(S.EXAMPLES):
        with cols[i % 2].container(border=True):
            st.caption(ex["kind"])
            if st.button(ex["text"], key=f"ex-{i}", type="tertiary"):
                _act(type="submit", text=ex["text"])
                st.rerun()


# ---------------------------------------------------------------- 처리 (그리기 뒤)

class Steps:
    """run 의 on_step 을 st.status 로 보여 준다."""

    def __init__(self, status, first_label: str):
        self.status, self.prev = status, None
        status.update(label=first_label)

    def __call__(self, name: str) -> None:
        if self.prev:
            self.status.write(f"✓ {self.prev}")
        self.prev = name
        self.status.update(label=f"{name}…")

    def done(self, label: str, tail: str) -> None:
        if self.prev:
            self.status.write(f"✓ {self.prev}")
        self.status.write(tail)
        self.status.update(label=label, state="complete", expanded=False)


def handle(conv: dict, action: dict) -> None:
    t = action["type"]
    if t == "revise":
        S.revise(conv, action["idx"])
        return
    if t == "submit":
        user_bubble(action["text"])
    with st.status("빈칸 나누는 중…", expanded=True) as box:
        steps = Steps(box, "백테스트 다시 실행 중…" if t == "backtest" else "빈칸 나누는 중…")
        if t == "submit":
            S.submit(conv, action["text"], S.default_run, steps)
            tail = "→ 백테스트는 '이대로 백테스트'를 누르면 보여 드립니다"
        elif t == "answer":
            S.answer(conv, action["reply"], S.default_run, steps)
            tail = "→ 백테스트는 '이대로 백테스트'를 누르면 보여 드립니다"
        else:
            S.backtest(conv, action["idx"], S.default_run, steps)
            tail = "✓ 백테스트"
        steps.done("완료", tail)


def main() -> None:
    conv = _conv()
    sidebar(conv)
    action = st.session_state.pop("action", None)
    prompt = st.chat_input("전략을 한국어로 말해 주세요")
    if prompt:
        action = {"type": "submit", "text": prompt}
    if not conv["messages"] and not action:
        empty_screen()
    else:
        draw_messages(conv)
    if action:
        try:
            handle(conv, action)
        except Exception as e:  # noqa: BLE001 — 화면은 죽지 않고 대화에 오류를 남긴다
            conv["messages"].append({"role": "assistant", "kind": "error", "text": f"처리 중 오류: {e}"})
        S.save(conv)
        st.rerun()


main()
