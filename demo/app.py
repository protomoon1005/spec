"""대화형 화면. 그리기만 한다 — 상태 처리는 ui.state, 판정·검사·백테스트는 core.pipeline.run.

실행: streamlit run app.py
"""
from __future__ import annotations

import html
import json
import re

import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from core.decide import DIRECT
from core.slot_fill import MODEL, ollama_status
from ui import state as S

st.set_page_config(page_title="전략 시험실", page_icon="📈", layout="centered", initial_sidebar_state="expanded")

ACCENT = "#2a78d6"
SERIES = {"strategy": ("전략", ACCENT), "kospi": ("코스피", "#eb6834"), "buy_hold": ("단순 보유", "#1baf7a")}
INK, MUTED = "#1f2328", "#6b7280"
UP, DOWN = "#16a34a", "#dc2626"                       # 사기·이익 초록, 팔기·손실 빨강
CLOSE_COLOR = "#4b5563"
LINE_COLORS = [ACCENT, "#eb6834", "#8b5cf6", "#0d9488"]
CHAT_KEY = "chat"

st.markdown(f"""<style>
.block-container {{ max-width: 760px; padding-top: 2.5rem; padding-bottom: 6rem; }}
.u-row {{ display: flex; justify-content: flex-end; margin: .75rem 0; }}
.u-bubble {{ background: #eef1f5; border-radius: 18px; padding: .6rem 1rem; max-width: 80%; white-space: pre-wrap; }}
.hero {{ text-align: center; font-size: 2rem; font-weight: 600; margin: 3.5rem 0 1.5rem; }}
.tag {{ font-size: .75rem; color: {MUTED}; background: #f1f2f4; border-radius: 6px; padding: 1px 6px; margin-left: 4px;
        white-space: nowrap; vertical-align: middle; }}
mark {{ background: #fde68a; border-radius: 4px; padding: 0 2px; }}
.muted {{ color: {MUTED}; font-size: .85rem; }}
.core {{ display: grid; grid-template-columns: max-content 1fr; gap: .55rem 1rem; margin: .35rem 0 .6rem;
         font-size: 1.12rem; line-height: 1.5; }}
.core .k {{ color: {MUTED}; font-size: .9rem; padding-top: .18rem; white-space: nowrap; }}
.rules {{ display: grid; grid-template-columns: max-content 1fr; gap: .45rem .9rem; font-size: .92rem; }}
.rules .k {{ color: {MUTED}; white-space: nowrap; }}
.small {{ color: {MUTED}; font-size: .8rem; margin-top: .2rem; }}
.small table {{ border-collapse: collapse; margin-top: .15rem; }}
.small td {{ padding: 0 .9rem 0 0; }}
.trades-wrap {{ max-height: 380px; overflow: auto; }}
table.trades {{ width: 100%; border-collapse: collapse; font-size: .88rem; font-variant-numeric: tabular-nums; }}
table.trades th {{ color: {MUTED}; font-weight: 500; text-align: left; border-bottom: 1px solid #e5e7eb;
                   padding: .35rem .5rem; position: sticky; top: 0; background: #fff; white-space: nowrap; }}
table.trades td {{ padding: .35rem .5rem; border-bottom: 1px solid #f1f2f4; white-space: nowrap; }}
table.trades .r {{ text-align: right; }}
table.trades .code {{ color: {MUTED}; font-size: .72rem; margin-left: 4px; }}
table.trades .up {{ color: {UP}; }}
table.trades .down {{ color: {DOWN}; }}
table.trades .open {{ color: {ACCENT}; }}
.chart-title {{ font-weight: 600; margin: .6rem 0 0; }}
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
    """지난 대화를 저장된 그대로 연다 (되묻기 답·접힌 결과 포함). 위젯 키에 대화 id 가 들어 있어 섞이지 않는다."""
    if (c := S.load(conv_id)) is not None:
        st.session_state.conv = c


def _new() -> None:
    st.session_state.conv = S.new_conversation()
    st.session_state.pop(CHAT_KEY, None)


def _key(conv: dict, *parts) -> str:
    return "-".join([conv["id"], *map(str, parts)])


# 아래 콜백은 버튼을 누른 직후, 화면을 다시 그리기 전에 돈다 (입력창 채우기는 이때만 된다).

def _retry(idx: int) -> None:
    """'조건 바꿔서 다시' · '고쳐 말하기': 원래 문장을 입력창에 채워 둔다."""
    st.session_state[CHAT_KEY] = S.retry_text(_conv(), idx)


def _revise(idx: int) -> None:
    conv = _conv()
    S.revise(conv, idx)
    S.save(conv)
    _retry(idx)


def _to_card(idx: int) -> None:
    conv = _conv()
    st.session_state.scroll_to = S.collapse_result(conv, idx)
    S.save(conv)


def _expand(idx: int) -> None:
    conv = _conv()
    S.expand_result(conv, idx)
    S.save(conv)


def _back() -> None:
    conv = _conv()
    S.back_to_previous_question(conv)
    S.save(conv)


def _scroll(conv: dict, idx: int) -> None:
    """전략 카드(st.container key → .st-key-… 클래스)로 스크롤."""
    sel = json.dumps(".st-key-" + _key(conv, "card", idx))
    st.html(f"""<script>
const d = window.frameElement ? window.parent.document : document;
setTimeout(() => {{ const el = d.querySelector({sel}); if (el) el.scrollIntoView({{behavior: "smooth", block: "start"}}); }}, 150);
</script>""", unsafe_allow_javascript=True)


# ---------------------------------------------------------------- 사이드바

def sidebar(conv: dict) -> None:
    with st.sidebar:
        st.button("＋ 새 전략", on_click=_new, width="stretch", type="primary")
        st.caption("지난 대화")
        items = S.list_history()
        if not items:
            st.markdown('<span class="muted">아직 없습니다.</span>', unsafe_allow_html=True)
        # 제목 아래 작은 날짜·시간: 버튼 라벨은 한 줄이라 버튼 키(.st-key-h-…)마다 ::after 로 붙인다.
        stamps = "".join(f'.st-key-h-{it["id"]} button p::after {{ content: {json.dumps(S.history_stamp(it))}; }}'
                         for it in items)
        st.markdown(f"""<style>
[class*="st-key-h-"] button {{ justify-content: flex-start; text-align: left; padding-top: .3rem; padding-bottom: .3rem; }}
[class*="st-key-h-"] button p::after {{ display: block; font-size: .72rem; color: {MUTED}; line-height: 1.2; }}
{stamps}</style>""", unsafe_allow_html=True)
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


def draw_clarify(conv: dict, m: dict, idx: int, live: bool) -> None:
    st.markdown(f"**확인할 게 있어요** &nbsp;<span class='tag'>되묻기 {m['round']}/{m['max_rounds']}</span>",
                unsafe_allow_html=True)
    picks, typed = {}, {}
    reply = m.get("reply") or {}
    for q in m["questions"]:
        st.markdown(q["text"])
        if q.get("hint"):
            st.caption(q["hint"])
        k = _key(conv, "q", idx, m.get("rev", 0), q["id"])
        prev = reply.get(q["id"])                       # 전에 고른 답 (지난 대화 · 이전 질문으로)
        default = prev if prev in q["options"] else (DIRECT if prev and DIRECT in q["options"] else None)
        picks[q["id"]] = st.pills(q["text"], q["options"], key=k, default=default,
                                  label_visibility="collapsed", disabled=not live)
        if picks[q["id"]] == DIRECT:
            typed[q["id"]] = st.text_input("직접 입력", key=k + "-t", placeholder="답을 적어 주세요",
                                           value=prev if default == DIRECT else "", disabled=not live)
    if live:
        c1, c2, _ = st.columns([1, 1.4, 2])
        if c1.button("답하기", key=_key(conv, "ans", idx), type="primary"):
            reply, err = S.collect_answers(m["questions"], picks, typed)
            if err:
                st.warning(err)
            else:
                _act(type="answer", reply=reply)
                st.rerun()
        if S.previous_question(conv) is not None:
            c2.button("← 이전 질문으로", key=_key(conv, "back", idx), on_click=_back)


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


def _tag(defaults: list[str]) -> str:
    """핵심 줄 옆 '기본값' 태그. 무엇이 기본값인지는 마우스를 올리면 보인다."""
    if not defaults:
        return ""
    return f'<span class="tag" title="{html.escape(" · ".join(defaults))}">기본값</span>'


def _md_bold(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def draw_understood(conv: dict, m: dict, idx: int) -> None:
    card = S.card_of(m)
    with st.container(border=True, key=_key(conv, "card", idx)):
        st.markdown("**AI가 이해한 전략**")
        rows = "".join(f'<div class="k">{html.escape(ln["key"])}</div>'
                       f'<div>{html.escape(ln["text"])}{_tag(ln["defaults"])}</div>' for ln in card["lines"])
        st.markdown(f'<div class="core">{rows}</div>', unsafe_allow_html=True)
        if card["other_defaults"]:
            st.caption("기본값 — " + " · ".join(card["other_defaults"]))

        with st.expander("공통 규칙"):
            out = []
            for c in card["common"]:
                if c.get("tax"):
                    tx = c["tax"]
                    table = "".join(
                        f'<div>{html.escape(t["label"])}</div><table><tr>'
                        + "".join(f"<td>{y}</td>" for y, _ in t["rows"]) + "</tr><tr>"
                        + "".join(f"<td>{r}</td>" for _, r in t["rows"]) + "</tr></table>"
                        for t in tx["table"])
                    body = html.escape(tx["headline"]) + (f'<div class="small">{table}</div>' if table else "")
                else:
                    body = _md_bold(_part(c))
                out.append(f'<div class="k">{html.escape(c["label"])}</div><div>{body}</div>')
            st.markdown(f'<div class="rules">{"".join(out)}</div>', unsafe_allow_html=True)

        title, open_ = S.checklist_summary(m["checklist"])
        with st.expander(title, expanded=open_):
            icon = {"passed": "✅", "failed": "⛔", "questions": "❓", "warned": "⚠️", "skipped": "➖"}
            for s in m["checklist"]:
                extra = " · ".join(s["issues"]) or s["note"]
                st.markdown(f"{icon[s['status']]} **{s['stage']}단계 {s['name']}** — {s['label']}"
                            + (f"  \n<span class='muted'>{html.escape(extra)}</span>" if extra else ""),
                            unsafe_allow_html=True)
        with st.expander("전략서 JSON"):
            st.json(m["spec"], expanded=True)
        used = m.get("used")
        with st.container(horizontal=True, horizontal_alignment="left", gap="small"):
            if st.button("이대로 백테스트", key=_key(conv, "bt", idx), type="primary", disabled=bool(used),
                         width="content"):
                _act(type="backtest", idx=idx)
                st.rerun()
            st.button("고쳐 말하기", key=_key(conv, "rv", idx), disabled=bool(used), on_click=_revise, args=(idx,),
                      width="content")


def _date_axes(fig: go.Figure) -> None:
    fig.update_xaxes(tickformat="%Y-%m", hoverformat="%Y-%m-%d", gridcolor="#f5f6f7")


def equity_chart(c: dict) -> go.Figure:
    fig = go.Figure()
    for key in ("strategy", "kospi", "buy_hold"):
        if c.get(key) is None:
            continue
        name, color = SERIES[key]
        fig.add_trace(go.Scatter(x=c["dates"], y=c[key], name=name, mode="lines",
                                 line=dict(color=color, width=2 if key == "strategy" else 1.5),
                                 hovertemplate="%{y:,.0f}원"))
    fig.update_layout(template="plotly_white", height=320, margin=dict(l=8, r=8, t=8, b=8), hovermode="x unified",
                      legend=dict(orientation="h", y=1.1, x=0, font=dict(color=MUTED)),
                      yaxis=dict(tickformat=",.0f", gridcolor="#eef0f2", ticksuffix="원"), font=dict(color=INK))
    _date_axes(fig)
    return fig


def _clip(dates: list[str], t: dict, lo: int, hi: int) -> tuple[list[str], dict]:
    """차트 값을 dates[lo..hi] 구간으로 자른다 (그리기용 — y축이 보이는 구간에 맞춰지도록). 매매 지점은 모두 구간 안."""
    cut = lambda v: v[lo:hi + 1]  # noqa: E731
    return cut(dates), {**t, "close": cut(t["close"]),
                        "price_lines": [{**ln, "values": cut(ln["values"])} for ln in t["price_lines"]],
                        "panels": [{**p, "lines": [{**ln, "values": cut(ln["values"])} for ln in p["lines"]]}
                                   for p in t["panels"]]}


def trade_chart(dates: list[str], t: dict) -> go.Figure:
    """위: 종가 + 가격 지표 + 사기·팔기 삼각형(체결일). 아래: 가격이 아닌 지표마다 보조 차트
    (기준선 + 사기·팔기 신호일 세로 점선, hover 에 신호일·체결일). x축 공유."""
    panels = t["panels"]
    rows = 1 + len(panels)
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.05,
                        row_heights=[3] + [1.1] * len(panels))
    fig.add_trace(go.Scatter(x=dates, y=t["close"], name="종가", mode="lines",
                             line=dict(color=CLOSE_COLOR, width=1.4), hovertemplate="%{y:,.0f}원"), row=1, col=1)
    for k, ln in enumerate(t["price_lines"]):
        fig.add_trace(go.Scatter(x=dates, y=ln["values"], name=ln["label"], mode="lines",
                                 line=dict(color=LINE_COLORS[k % len(LINE_COLORS)], width=1.2),
                                 hovertemplate="%{y:,.0f}원"), row=1, col=1)
    for v in t["price_refs"]:
        fig.add_hline(y=v, line=dict(color=MUTED, width=1, dash="dash"), row=1, col=1,
                      annotation_text=f"{v:,.0f}", annotation_font_color=MUTED)
    for side, sym, label, color in (("buys", "triangle-up", "사기", UP), ("sells", "triangle-down", "팔기", DOWN)):
        pts = t[side]
        if pts:
            fig.add_trace(go.Scatter(x=[p["date"] for p in pts], y=[p["price"] for p in pts], name=label,
                                     mode="markers", marker=dict(symbol=sym, size=10, color=color),
                                     hovertemplate="%{y:,.0f}원 체결"), row=1, col=1)
    k0 = len(t["price_lines"])
    for r, p in enumerate(panels, start=2):
        for k, ln in enumerate(p["lines"]):
            fig.add_trace(go.Scatter(x=dates, y=ln["values"], name=ln["label"], mode="lines",
                                     line=dict(color=LINE_COLORS[(k0 + k) % len(LINE_COLORS)], width=1.2),
                                     hovertemplate="%{y:,.2f}"), row=r, col=1)
        # 세로 점선은 신호가 난 날(종가로 판단한 날). hover 를 띄우려고 add_vline 대신 선 trace 로 그린다.
        ys = [y for ln in p["lines"] for y in ln["values"] if y is not None] + list(p["refs"])
        if ys:
            lo, hi = min(ys), max(ys)
            for side, label, color in (("buys", "사기", UP), ("sells", "팔기", DOWN)):
                pts = [pt for pt in t[side] if pt.get("signal_date")]   # 첫날 매수 등 신호 없는 체결은 선 없음
                if not pts:
                    continue
                xs, yv, tx = [], [], []
                for pt in pts:
                    xs += [pt["signal_date"], pt["signal_date"], None]
                    yv += [lo, hi, None]
                    tx += [S.signal_hover(pt)] * 2 + [None]
                fig.add_trace(go.Scatter(x=xs, y=yv, name=f"{label} 신호", mode="lines", showlegend=False,
                                         line=dict(color=color, width=1, dash="dot"), opacity=0.6,
                                         text=tx, hovertemplate="%{text}<extra></extra>"), row=r, col=1)
        for v in p["refs"]:
            fig.add_hline(y=v, line=dict(color=MUTED, width=1, dash="dash"), row=r, col=1,
                          annotation_text=f"{v:g}", annotation_font_color=MUTED)
        k0 += len(p["lines"])
    fig.update_layout(template="plotly_white", height=300 + 120 * len(panels), margin=dict(l=8, r=8, t=8, b=8),
                      hovermode="x unified", legend=dict(orientation="h", y=1.08, x=0, font=dict(color=MUTED)),
                      font=dict(color=INK))
    fig.update_yaxes(gridcolor="#eef0f2")
    fig.update_yaxes(tickformat=",.0f", ticksuffix="원", row=1, col=1)
    _date_axes(fig)
    return fig


def trades_table(rows: list[dict]) -> str:
    head = ("<tr><th>종목</th><th>산 날</th><th class='r'>산 값</th><th>판 날</th><th class='r'>판 값</th>"
            "<th class='r'>보유일</th><th class='r'>수익률</th></tr>")
    body = []
    for r in rows:
        ret = r["return_pct"]
        tone = "" if ret is None or ret == 0 else ("up" if ret > 0 else "down")
        exit_date = "<span class='open'>보유 중</span>" if r["open"] else html.escape(r["exit_date"])
        note = " <span class='code'>평가</span>" if r["open"] else ""
        body.append(
            f"<tr><td>{html.escape(r['name'])}<span class='code'>{html.escape(r['ticker'])}</span></td>"
            f"<td>{r['entry_date']}</td><td class='r'>{S.fmt_won(r['entry_price'])}</td>"
            f"<td>{exit_date}</td><td class='r'>{S.fmt_won(r['exit_price'])}</td>"
            f"<td class='r'>{r['holding_days']}일</td><td class='r {tone}'>{S.fmt_ret(ret)}{note}</td></tr>")
    return f"<div class='trades-wrap'><table class='trades'><thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>"


def draw_result(conv: dict, m: dict, idx: int) -> None:
    mt = m["metrics"]
    with st.container(border=True, key=_key(conv, "result", idx)):
        if m.get("collapsed"):
            c1, c2 = st.columns([3, 1], vertical_alignment="center")
            c1.markdown(f"**백테스트 결과** <span class='muted'>(접음) 총수익률 {S.fmt_pct(mt['total_return'])} · "
                        f"MDD {S.fmt_pct(mt['mdd'], signed=False)} · 거래 {mt['n_trades']}회</span>",
                        unsafe_allow_html=True)
            c2.button("결과 펼치기", key=_key(conv, "expand", idx), on_click=_expand, args=(idx,), width="stretch")
        else:
            _result_body(conv, m, idx)
    c1, c2, c3 = st.columns(3)
    c1.button("조건 바꿔서 다시", key=_key(conv, "retry", idx), on_click=_retry, args=(idx,), width="stretch",
              help="원래 문장을 입력창에 채워 둡니다.")
    c2.button("전략 카드로 돌아가기", key=_key(conv, "tocard", idx), on_click=_to_card, args=(idx,), width="stretch")
    c3.button("처음으로", key=_key(conv, "home", idx), on_click=_new, width="stretch")
    done = bool(m.get("baseline_done"))
    if st.button("LLM 코드 생성과 비교" + (" (비교함)" if done else ""), key=_key(conv, "baseline", idx),
                 disabled=done, width="stretch",
                 help=f"같은 문장으로 LLM 이 파이썬 코드를 직접 짜게 해 {S.BASELINE_REPEATS}번 돌리고, 우리 결과와 나란히 보여 줍니다."):
        _act(type="baseline", idx=idx)
        st.rerun()


def orders_table(rows: list[dict]) -> str:
    head = "<tr><th>판단일</th><th>종목</th><th>방향</th><th class='r'>목표 비중</th></tr>"
    body = "".join(
        f"<tr><td>{r['date']}</td><td>{html.escape(r['name'])}<span class='code'>{html.escape(r['ticker'])}</span></td>"
        f"<td class='{'up' if r['side'] == '사기' else 'down'}'>{r['side']}</td><td class='r'>{r['weight']:.1%}</td></tr>"
        for r in rows)
    return f"<div class='trades-wrap'><table class='trades'><thead>{head}</thead><tbody>{body}</tbody></table></div>"


def baseline_chart(c: dict) -> go.Figure:
    fig = go.Figure()
    for key, name, color, width in (("ours", "우리 방식", ACCENT, 2), ("base", "LLM 코드", "#8b5cf6", 1.6)):
        fig.add_trace(go.Scatter(x=c["dates"], y=c[key], name=name, mode="lines", line=dict(color=color, width=width),
                                 hovertemplate="%{y:,.0f}원"))
    fig.update_layout(template="plotly_white", height=300, margin=dict(l=8, r=8, t=8, b=8), hovermode="x unified",
                      legend=dict(orientation="h", y=1.1, x=0, font=dict(color=MUTED)),
                      yaxis=dict(tickformat=",.0f", gridcolor="#eef0f2", ticksuffix="원"), font=dict(color=INK))
    _date_axes(fig)
    return fig


def draw_baseline(conv: dict, m: dict, idx: int) -> None:
    with st.container(border=True, key=_key(conv, "bl", idx)):
        st.markdown("**LLM 코드 생성과 비교** <span class='muted'>같은 모델·같은 설정, 프롬프트에는 데이터·출력 형식만</span>",
                    unsafe_allow_html=True)
        with st.container(horizontal=True, gap="small"):
            for text, color in S.baseline_badges(m):
                st.badge(text, color=color)
        if m["outcome"] in ("clarify", "reject"):
            st.markdown(f"LLM 의 답: {html.escape(m['message'])}")
        mt = m.get("metrics")
        if m.get("chart"):
            a, b = st.columns(2)
            a.metric("우리 방식 총수익률", S.fmt_pct(mt["ours_total_return"]))
            b.metric("LLM 코드 총수익률", S.fmt_pct(mt["base_total_return"]),
                     help="LLM 코드의 주문을 우리 엔진과 같은 비용·세금(다음 날 시가 체결)으로 체결했습니다.")
            st.plotly_chart(baseline_chart(m["chart"]), width="stretch", key=_key(conv, "bleq", idx),
                            config={"displayModeBar": False})
            if mt["ignored"] or mt["unfilled"]:
                st.caption(f"기간 밖이라 버린 주문 {mt['ignored']}건 · 기간 끝이라 체결 못 한 주문 {mt['unfilled']}건")
        left, right = st.columns(2)
        ours, base = S.order_rows(m["ours_orders"]), S.order_rows(m.get("base_orders"))
        with left:
            st.markdown(f"<div class='chart-title'>우리 주문 ({len(m['ours_orders'])}건)</div>", unsafe_allow_html=True)
            st.markdown(orders_table(ours) if ours else "<span class='muted'>주문 없음</span>", unsafe_allow_html=True)
        with right:
            n = len(m["base_orders"]) if m.get("base_orders") is not None else None
            st.markdown(f"<div class='chart-title'>LLM 코드 주문 ({'—' if n is None else f'{n}건'})</div>",
                        unsafe_allow_html=True)
            st.markdown(orders_table(base) if base else
                        f"<span class='muted'>{'실행하지 못해 주문이 없습니다' if n is None else '주문 없음'}</span>",
                        unsafe_allow_html=True)
        if m.get("code"):
            with st.expander("생성된 코드 (1회차)"):
                st.code(m["code"], language="python")
        elif m.get("raw"):
            with st.expander("LLM 응답 원문 (1회차)"):
                st.text(m["raw"])
        with st.expander(f"{len(m['runs'])}회 실행 · 미래 참조 검사"):
            label = {"code_ok": "실행 성공", "code_fail": "실행 실패", "clarify": "되물음", "reject": "구현 불가",
                     "error": "응답 이상"}
            for k, r in enumerate(m["runs"], 1):
                extra = f"주문 {r['n_orders']}건" if r["n_orders"] is not None else (r["error"] or "")
                code = f" · 코드 {r['code_hash']}" if r["code_hash"] else ""
                st.markdown(f"- {k}회: {label[r['outcome']]} · {html.escape(extra[:160])}{code} · {r['seconds']}초")
            la = m.get("lookahead")
            if la:
                st.markdown("미래 참조 검사 (그날 이후 가격만 바꿔 다시 실행 → 그날까지 주문이 같아야 함)")
                for c in la["cuts"]:
                    st.markdown(f"- {c['cut']}: {html.escape(c['detail'] or c['status'])}")
        st.caption(f"baseline 에 넘긴 기간: {m['period'][0]} ~ {m['period'][1]} (기본 기간) · 실행: 별도 프로세스, "
                   "pandas·numpy 만, 파일·네트워크 금지, 30초 제한"
                   + (f" · 받지 못한 데이터 {', '.join(m['missing'])}" if m.get("missing") else ""))


def _result_body(conv: dict, m: dict, idx: int) -> None:
    mt = m["metrics"]
    st.markdown(f"**백테스트 결과** <span class='muted'>{mt['start']} ~ {mt['end']}</span>", unsafe_allow_html=True)
    a, b, c, d = st.columns(4)
    a.metric("총수익률", S.fmt_pct(mt["total_return"]))
    b.metric("연 수익률", S.fmt_pct(mt["cagr"]))
    c.metric("MDD", S.fmt_pct(mt["mdd"], signed=False))
    d.metric("거래 횟수", f"{mt['n_trades']}회", help=f"보유 중 {mt['n_open_positions']}건은 빼고 셉니다.")
    st.caption(f"비교 — 코스피 {S.fmt_pct(mt['kospi_total_return'])} · 단순 보유 {S.fmt_pct(mt['buy_hold_total_return'])}")

    st.markdown("<div class='chart-title'>자산 곡선</div>", unsafe_allow_html=True)
    st.plotly_chart(equity_chart(m["chart"]), width="stretch", key=_key(conv, "eq", idx),
                    config={"displayModeBar": False})
    st.markdown("<div class='chart-title'>매매 지점</div>", unsafe_allow_html=True)
    tcs = m.get("trade_chart")
    if not tcs:
        st.caption("이 기록에는 매매 지점 데이터가 없습니다 (이전 판에서 저장한 결과).")
    else:
        dates = m["chart"]["dates"]
        windows = [S.trade_window(dates, t) for t in tcs]
        full = False
        if any(w is not None for w in windows):           # 매매가 없으면 토글 없이 전체 기간
            full = st.toggle("전체 기간 보기", key=_key(conv, "full", idx),
                             help="처음에는 첫 매매 20거래일 전 ~ 마지막 매매 20거래일 후만 보여 줍니다.")
        tabs = st.tabs([t["name"] for t in tcs]) if len(tcs) > 1 else [st.container()]
        for tab, t, w in zip(tabs, tcs, windows):
            with tab:
                d, tv = (dates, t) if full or w is None else _clip(dates, t, *w)
                st.plotly_chart(trade_chart(d, tv), width="stretch",
                                key=_key(conv, "tc", idx, t["ticker"]), config={"displayModeBar": False})

    for w in m["warnings"]:
        st.warning(w, icon="⚠️")
    rows = S.trade_rows(m["trades"])
    with st.expander(f"거래 내역 ({len(rows)}건)"):
        if rows:
            st.markdown(trades_table(rows), unsafe_allow_html=True)
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
            draw_clarify(conv, m, i, live=(i == last_clarify and not m.get("done")))
        elif k == "reject":
            draw_reject(m)
        elif k == "understood":
            draw_understood(conv, m, i)
        elif k == "result":
            draw_result(conv, m, i)
        elif k == "baseline":
            draw_baseline(conv, m, i)
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
    if t == "submit":
        user_bubble(action["text"])
    with st.status("빈칸 나누는 중…", expanded=True) as box:
        first = {"backtest": "백테스트 다시 실행 중…", "baseline": "LLM 코드 생성과 비교 중…"}.get(t, "빈칸 나누는 중…")
        steps = Steps(box, first)
        if t == "baseline":
            S.baseline_compare(conv, action["idx"], S.default_run, steps)
            steps.done("완료", "✓ 비교")
            return
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
    prompt = st.chat_input("전략을 한국어로 말해 주세요", key=CHAT_KEY)
    if prompt:
        action = {"type": "submit", "text": prompt}
    if not conv["messages"] and not action:
        empty_screen()
    else:
        draw_messages(conv)
    if (target := st.session_state.pop("scroll_to", None)) is not None:
        _scroll(conv, target)
    if action:
        try:
            handle(conv, action)
        except Exception as e:  # noqa: BLE001 — 화면은 죽지 않고 대화에 오류를 남긴다
            conv["messages"].append({"role": "assistant", "kind": "error", "text": f"처리 중 오류: {e}"})
        S.save(conv)
        st.rerun()


main()
