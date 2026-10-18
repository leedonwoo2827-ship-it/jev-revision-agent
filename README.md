# law-errata-jev

법령이 개정되면 **우리 교재의 어느 문장을 고쳐야 하는지** 골라내고, 수정할 확률과 그대로 둬도 되는 확률을 표로 보여 주는 정오표 도구입니다.
검토자가 HTML 화면에서 항목을 체크하고 **[반영]**을 누르면 아래 세 가지가 만들어집니다.

- 반영내역
- 반영된 원고
- 게시판에 올릴 정오표

판정은 **JEV** 방식으로 합니다. 판정 엔진은 **Codex CLI**이며, ChatGPT 계정 OAuth로 로그인해서 쓰므로 API 키가 필요 없습니다.

| 단계 | 하는 일 |
|---|---|
| **J**udge | 변경 조문이 교재의 어느 문구와 관련되는지 찾고, 영향 유형을 정합니다. 유형은 수치변경, 용어변경, 내용변경, 신설조문누락, 삭제조문잔존, 무관 중 하나입니다. |
| **E**vidence | 교재 원문, 구 조문, 신 조문을 그대로 인용합니다. |
| **V**erdict | 수정확률(`p_revise`)과 유지확률(`1 − p_revise`), 기존문구 → 수정문구 초안을 냅니다. |

> 이 저장소에는 **코드만** 올립니다. 교재 PDF, 법령 원문, 판정 결과, 반영 산출물은 `.gitignore`로 제외되어 있습니다. 대상 폴더는 `_context/`, `work/`, `output/`입니다.

## 흐름

```
_context/교재.pdf ──extract_book──▶ 장 범위 쪽별 텍스트 (예: 9장 p.386–401)
_context/law_old_*.  ─┐
_context/law_new_*.  ─┴─diff_law──▶ 변경 조문 목록 (LLM 없이 조문 단위로 비교)
                         judge_codex ──▶ Codex OAuth로 조문마다 JEV 판정, 중복 정리
                         report ──▶ output/errata_ch09.html (검토 화면)
                         [반영] ──▶ output/applied/ 반영내역 · 반영본 · 게시판 정오표
```

## 설치 (처음 한 번)

1. Python 3.10 이상과 Node.js를 설치합니다.
2. `setup.bat`을 더블클릭합니다. 다음 작업이 자동으로 진행됩니다.
   - 가상환경을 만들고 `pymupdf`, `pyyaml`을 설치합니다.
   - Codex CLI가 없으면 설치합니다 (`npm i -g @openai/codex`).
   - `codex login`을 실행합니다. 브라우저가 열리면 ChatGPT 계정으로 로그인하세요. 이것이 OAuth 로그인입니다.

## `_context`에 넣을 파일

| 파일 | 내용 | 받는 곳 |
|---|---|---|
| `2025 경영지도사 1차_중소기업관계법령(1교).pdf` | 교재 원본 (쪼개지 않아도 됩니다) | 사내 |
| `law_new_법.pdf` | 현행 소상공인기본법 | law.go.kr → 법령 화면 → 저장 → 선택저장(PDF), 시행예정조문 포함 |
| `law_new_령.pdf` | 현행 시행령 | 같은 방법 |
| `law_old_법.pdf` | **책이 기준으로 삼은** 법률 (책 p.11 [법령적용기준]: 2021.3.9. 법률 제17623호) | 연혁 탭에서 해당 버전 선택 → 저장 |
| `law_old_령.pdf` | 책이 기준으로 삼은 시행령 (2021.12.30. 대통령령 제32274호) | 같은 방법 |

- 파일 형식은 PDF를 권장합니다. HWPX와 TXT도 읽을 수 있습니다. HWP(구형 바이너리)는 읽을 수 없으니 HWPX나 PDF로 저장하세요.
- 위 법령 파일 네 개는 직접 저장하지 않고 법제처에서 바로 받아올 수도 있습니다. 받아온 파일은 `_context/*.txt`로 저장됩니다.

```
.venv\Scripts\python -m jev.fetch_law "소상공인기본법" new 법
.venv\Scripts\python -m jev.fetch_law "소상공인기본법" old 법 17623,20201208
.venv\Scripts\python -m jev.fetch_law "소상공인기본법시행령" new 령
.venv\Scripts\python -m jev.fetch_law "소상공인기본법시행령" old 령 32274,20211228
```

같은 이름의 PDF가 있으면 PDF를 먼저 씁니다. 우선순위는 PDF, HWPX, TXT 순입니다.

## 실행

- `run.bat`: 9장 전체를 처리합니다. 추출, 비교, Codex 판정, HTML 생성을 거쳐 브라우저 검토 화면(`http://127.0.0.1:8765`)을 엽니다.
- `run.bat ch10`: 다른 장을 처리합니다. 먼저 `chapters.yaml`에 그 장의 쪽 범위와 법령 파일명을 추가해야 합니다.
- `review.bat`: 판정을 다시 하지 않고 검토 화면만 다시 엽니다.

Codex 판정 결과는 `work/<ch>/cache/`에 저장됩니다. 그래서 다시 실행해도 바뀐 조문만 새로 판정합니다.

### 검토 화면

- **수정확률 / 유지확률** 열이 있습니다. 수정확률이 60% 이상인 항목은 처음부터 체크되어 있습니다.
- 수정문구는 화면에서 바로 고칠 수 있고, 항목마다 메모를 남길 수 있습니다.
- `Evidence`를 펼치면 교재 원문, 구 조문, 신 조문 인용을 볼 수 있습니다.
- 반영자 이름을 입력하고 **[반영]**을 누르면 아래 파일이 `output/applied/`에 만들어집니다.

| 파일 | 용도 |
|---|---|
| `반영내역_ch09_<시각>.md / .csv` | 전체 항목의 반영·미반영 상태, 확률, 기존→수정, 메모 |
| `ch09_revised_<시각>.md / .html` | 반영된 원고 (HTML에서는 삭제·추가 부분이 표시됨) |
| `정오표_게시판_ch09_<시각>.html` | **회사 게시판 업로드용** 정오표 (반영한 항목만) |

서버 없이 HTML 파일만 열었다면 [반영]을 눌렀을 때 `decisions_ch09.json` 파일이 내려받아집니다. 이 파일은 다음 명령으로 처리합니다.

```
.venv\Scripts\python -m jev.apply decisions_ch09.json
```

## 구성

```
setup.bat / run.bat / review.bat
chapters.yaml          장별 쪽 범위 · 법령 파일 설정
jev/extract_book.py    교재 장 추출 (+ p.11 법령적용기준 줄)
jev/extract_law.py     법령 PDF/HWPX/TXT를 조문·장 단위로 파싱 (부칙 분리, 개정 태그 무시)
jev/diff_law.py        구/신 조문 비교 → changes.json
jev/fetch_law.py       (선택) 법제처 본문 받기
jev/judge_codex.py     codex exec --output-schema 로 JEV 판정, 중복 정리, 캐시
jev/report.py          검토 HTML 생성
jev/server.py          127.0.0.1 로컬 서버 (POST /apply)
jev/apply.py           반영 처리 (반영내역 / 반영본 / 게시판 정오표)
```

## 주의

- 확률은 Codex가 판단한 값입니다. 최종 반영 여부는 반드시 사람이 확인하세요.
- 책 쪽번호는 PDF 쪽번호와 같다고 가정합니다. 9장 교재 기준으로 확인했습니다.
- 수정문구 위치는 교재 텍스트에서 **공백을 무시하고** 찾습니다. 위치를 찾지 못한 항목은 반영내역에 "수동반영 필요"로 표시됩니다.
