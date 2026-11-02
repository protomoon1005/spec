# 평가 데이터셋 v0.2 (초안, 2인 라벨링 전)

## 무엇인가

- 한국어 투자 전략 문장을 전략서 v0.2 로 바꾸고 판정(ok, ask, reject)하는 기능의 평가 데이터셋 초안이다.
- 120건: ok 60(자산 배분형 25, 매매 조건형 35), ask 30, reject 30.
- split: dev 40(ok 20, ask 10, reject 10), test 80(ok 40, ask 20, reject 20).
- 평가 지표: 실행 결과 기준 정확도(정답 전략서와 시스템 전략서로 각각 백테스트해 주문이 같으면 정답. 거래일, 종목, 방향이 같고 목표 비중 차이 0.5%p 이내), 되묻기 판정 재현율, 거절 판정 정확도.
- 규칙은 2026-11-01 에서 2026-11-02 사이 팀 확정본을 따른다. 라벨링 기준은 GUIDE.md 에 있다.
- 아직 라벨러 2명의 독립 검토 전이다. 정답은 초안 작성자 판단이다.

## 파일

- dataset.jsonl: 데이터셋 본체. 한 줄에 한 건. 칸은 id, split, type, text, expected_spec, defaults_used, expected_ask, expected_reject, evidence, tags, note.
- review.csv: 엑셀 검토용(UTF-8 BOM). id, split, type, text, 정답 요약, 라벨러A 판정, 라벨러B 판정, 비고. 라벨러 칸은 비어 있다.
- GUIDE.md: 라벨링 가이드(규칙 요약과 애매한 경우 판단 원칙).
- gen.py: dataset.jsonl 과 review.csv 를 만드는 스크립트. 문장과 정답은 여기서 고친다.
- check.py: dataset.jsonl 검사 스크립트. 건수, 비율, split 배분, 필수 칸, 종목코드, 지표와 기간, 연산자, 지수 주체, 보유일, 비중과 상한, 배당 낱말(부정문과 종목명 속 낱말 처리 포함), selector 정답 종목, NO_DATA 종목 상장일, 새 규칙 적용 건수를 본다.
- resolve.py: selector(업종, 시장 단위 고르기)를 실제 종목 목록으로 푸는 코드와 배당 낱말 판정. gen.py 와 check.py 가 같이 쓴다.
- ref/sector_KOSPI_20230102.csv, ref/sector_KOSDAQ_20230102.csv: 업종 판정용 거래소 업종 분류(백테스트 시작일 기준).
- ref/sector_KOSPI_20251230.csv, ref/sector_KOSDAQ_20251230.csv: 고르기 후보 프록시(이 목록에 있는 종목만 가격 데이터가 있다고 본다).
- ref/kind_listing.csv: KIND 상장 법인 목록(회사명, 시장구분, 종목코드, 상장일). NO_DATA 검사에 쓴다.
- cache/cap_KOSPI_20221229.csv, cache/cap_KOSDAQ_20221229.csv: 시작 직전 거래일 시가총액.
- cache/days.txt: 시작 전 61거래일(2022-10-05 에서 2022-12-29).
- cache/ohlcv_pre.csv.gz: 위 61거래일 중 첫 날과 마지막 21거래일의 종목별 종가, 거래대금(주식만).
- ETF 목록은 저장소의 ../data/listing.csv 를 읽는다. 이 폴더는 저장소 eval/ 에 있어야 한다.

## 데이터 출처와 주의

- cache/ 와 ref/ 는 2026-11-02 에 한 번 받은 사본이다. 다시 받지 않는다.
- KRX(pykrx 포함) 자동 조회는 약관상 금지이고 IP 차단 대상이다. 이 폴더의 스크립트는 네트워크를 쓰지 않는다. 데이터를 새로 받는 코드를 추가하지 않는다.

## 재생성과 검사

- 필요 환경: Python 3.10 이상, 표준 라이브러리만 쓴다. pandas 등 외부 패키지는 필요 없다.
- 재생성: python3 eval/gen.py
- 검사: python3 eval/check.py
- 다른 파일 검사: python3 eval/check.py 경로/다른.jsonl
- 두 스크립트는 경로를 스크립트 위치 기준으로 잡으므로 어느 디렉터리에서 실행해도 된다.
- check.py 는 실패가 있으면 종료 코드 1 을 낸다. 문장이나 정답을 고친 뒤에는 gen.py, check.py 순서로 돌린다.
- dataset.jsonl 을 손으로 고치지 않는다. gen.py 와 어긋난다.

## 라벨링 절차

- 라벨러 A, B 가 review.csv 사본을 각자 받아 따로 판정한다. 서로 상의하지 않는다.
- 판정할 때는 text 만 먼저 보고 판정을 적은 다음 정답 요약과 비교한다. 다르면 비고에 이유를 적는다.
- 판정 칸에는 ok, ask(slot 이름), reject(코드) 중 하나를 적는다. ok 인데 정답과 값이 다르면 어느 칸인지 적는다.
- 두 사람의 판정을 모아 일치율을 기록한다. 유형(ok, ask, reject) 일치율과, ok 건의 정답 전략서 일치율을 따로 적는다.
- 불일치 건은 두 사람과 팀이 함께 토론해 정답을 정한다. 규칙이 모자라서 갈린 건은 GUIDE.md 와 규칙 문서를 고치고 기록한다.
- 확정한 정답은 gen.py 에 반영하고 다시 생성한 뒤 check.py 를 통과시킨다. 버전을 올린다.
- test 80건은 시스템 개발 중에 열어보지 않는다. 문장, 정답, 결과를 개발 판단에 쓰지 않는다. 개발과 조정은 dev 40건으로만 한다.
- test 는 마지막 평가에서 한 번만 쓴다. 한 번 쓴 뒤 결과를 보고 시스템을 고쳐 다시 재면 그 숫자는 test 결과로 보고하지 않는다.
- 라벨러 검토는 test 를 포함한 120건 전체에 하지만, 라벨러는 시스템 개발에 참여하지 않는 사람이 맡거나, 맡더라도 test 문장을 개발에 쓰지 않는다.
