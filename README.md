# LinkUp: LLM 기반 웹 UI/UX 가이드라인 자동 검수 시스템

정부 UI/UX 가이드라인(PDF)을 근거 문서로 삼아, 웹페이지의 스크린샷과 HTML을 멀티모달 LLM(Gemini 2.5 Flash)으로 분석하고 디자인·운영·기술 영역의 결함을 보고하는 시스템입니다. 가이드라인은 ChromaDB 벡터 저장소에 색인되어 검색 증강(RAG) 컨텍스트로 프롬프트에 포함됩니다.

이 저장소는 (1) 웹 서비스 코드와 (2) 논문의 Results 절에 보고한 실험의 스크립트·데이터·결과를 함께 담고 있습니다.

## 저장소 구성

```
main.py, run.py            FastAPI 백엔드 (분석 API, SSE 진행률 스트리밍)
cli.py                     CI/CD용 명령줄 검수 도구 (실험 실행에도 사용)
services/
  analyzer.py              크롤링 → 스크린샷/HTML 수집 → RAG 검색 → Gemini 분석 → 리포트 병합
  indexer.py               가이드라인 PDF를 청크로 나눠 ./chroma_db 에 색인
  scraper.py               Playwright 수집 유틸리티
frontend/                  Streamlit 웹 UI
data/                      가이드라인 PDF (gov_guide.pdf, gov_check.pdf)
.github/workflows/         cli.py 를 이용한 GitHub Actions 검수 예시

testbed_200/               평가용 테스트베드 200페이지 + 정답 메타데이터(dataset_metadata.json)
testbed/                   구간별 지연시간 측정용 50페이지
experiments/               논문 실험 스크립트
paper_results/             논문 수치의 근거가 되는 실행 결과
```

## 설치

Python 3.10 이상에서 동작을 확인했습니다.

```bash
python -m venv venv
venv\Scripts\activate            # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

프로젝트 루트에 `.env` 파일을 만들고 Gemini API 키를 넣습니다.

```
GEMINI_API_KEY=your_key_here
```

가이드라인 벡터 DB를 생성합니다(최초 1회, `./chroma_db` 생성).

```bash
python services/indexer.py
```

## 웹 서비스 실행

```bash
python run.py                       # 백엔드: http://127.0.0.1:8000
streamlit run frontend/app.py       # 프론트엔드 (별도 터미널)
```

명령줄에서 단일 사이트를 검수하려면 다음을 실행합니다.

```bash
python cli.py --url https://example.com --threshold 70 --output audit_report.json
```

## 논문 실험 재현

모든 스크립트는 **저장소 루트에서** `python experiments/<script>.py` 형태로 실행합니다. 스크립트는 기본적으로 `./results/` 에 결과를 씁니다(`.gitignore` 대상). `RESULTS_DIR` 환경변수를 지원하는 스크립트는 이를 `paper_results/run2` 로 지정해 보관된 리포트를 다시 채점할 수 있습니다.

```bash
pip install -r experiments/requirements.txt
```

### 실험과 파일 대응

| 논문 절 | 내용 | 스크립트 | 보관 결과 |
|---|---|---|---|
| 실험 설정 | 200페이지 테스트베드 생성 (10개 템플릿 × 5개 결함 유형, 대조군 39개) | `generate_200_testbed.py` | `testbed_200/` |
| 실험 설정 | CLI로 200페이지 순차 분석 (임계값 0) | `run_batch_v2.py` | `paper_results/run2/` (리포트 200개 + `run_manifest.json`) |
| 정량적 탐지 성능 (Table: performance, category) | 키워드 채점 | `evaluate_performance_v5.py` | 표준 출력 |
| 〃 | 의미 기반 채점 (LLM 분류) | `semantic_issue_classifier.py` | `paper_results/run1/semantic_scoring_run1.json` |
| 반복 실행 (Table: repeat run) | 두 번째 실행의 키워드 채점 | `evaluate_performance_v5.py` | `paper_results/run2/` |
| axe-core 비교 | axe-core v4.14.0 기본 구성 스캔 | `run_axe_core_scan.py` | `paper_results/axe_core/axe_core_scan.json` |
| 〃 | id 중복 룰 비활성화 원인 진단 (`page_065`) | `diagnose_axe_duplicate_id.py` | 표준 출력 |
| 입력 모달리티 Ablation | 스크린샷+HTML / 스크린샷만 / HTML만 (57페이지) | `modality_ablation.py` | `paper_results/modality_ablation/` |
| 처리 속도 (종단) | CLI 1회 실행 시간 | `run_batch_v2.py` | `paper_results/run2/run_manifest.json` (`elapsed_sec`) |
| 처리 속도 (구간별) | 50페이지 × 3회, `file://` 로컬 환경 | `measure_latency.py` | `paper_results/latency/` |
| Discussion: 점수·근거 필드 | 점검 항목 수, 점수 분포, 근거 파일명 점검 | `audit_pipeline_artifacts.py` | 표준 출력 |
| Discussion: 명도 대비 수치 | 보고된 대비비와 WCAG 공식값 비교 | `audit_contrast_ratios.py` | `paper_results/run1/contrast_audit_run1.csv` |
| Discussion: RAG | 고정 검색 질의가 가져오는 청크 확인 | `show_fixed_retrieval.py` | 표준 출력 |
| Discussion: RAG (예비) | 표준 용어 위반 45페이지, RAG 포함/제외 비교 | `rag_ablation.py` | `paper_results/rag_ablation/` |

### 실행 예시

**1. 테스트베드 분석 (탐지 성능, 종단 지연시간)**

```bash
python -m http.server 8080                  # 터미널 1: 루트에서 테스트베드 제공
python experiments/run_batch_v2.py          # 터미널 2: results/report_page_XXX.json 생성
python experiments/evaluate_performance_v5.py
python experiments/semantic_issue_classifier.py
```

보관된 두 번째 실행 결과를 다시 채점하려면:

```bash
RESULTS_DIR=paper_results/run2 python experiments/evaluate_performance_v5.py
# Windows PowerShell: $env:RESULTS_DIR="paper_results/run2"; python experiments/evaluate_performance_v5.py
```

**2. axe-core 비교**

[axe-core v4.14.0](https://github.com/dequelabs/axe-core/releases/tag/v4.14.0)의 `axe.min.js` 를 저장소 루트에 내려받은 뒤 실행합니다(`npm pack axe-core@4.14.0` 으로도 받을 수 있습니다).

```bash
python experiments/run_axe_core_scan.py
python experiments/diagnose_axe_duplicate_id.py
```

**3. 입력 모달리티 Ablation**

논문과 같은 표본을 쓰려면 보관된 `sample.json` 을 먼저 복사합니다. 이 파일이 있으면 스크립트가 새로 추출하지 않고 이 표본을 사용합니다.

```bash
mkdir -p results/modality_ablation
cp paper_results/modality_ablation/sample.json results/modality_ablation/
python experiments/modality_ablation.py run
python experiments/modality_ablation.py score
```

**4. 구간별 지연시간 / RAG 예비 실험**

이 두 스크립트는 벡터 DB 경로 기본값이 `./services/chroma_db` 입니다. `indexer.py` 로 만든 DB를 쓰려면 경로를 지정합니다.

```bash
CHROMA_DB_PATH=./chroma_db python experiments/measure_latency.py
CHROMA_DB_PATH=./chroma_db python experiments/rag_ablation.py
```

## 결과 데이터에 대한 참고

- **첫 번째 실행**의 원본 리포트는 두 번째 실행에 덮어써져 보존되지 않았습니다. `paper_results/run1/` 에는 첫 번째 실행 직후 생성한 의미 기반 채점 결과와 명도 대비 감사 결과만 있습니다. 첫 번째 실행의 키워드 채점 수치는 실행 직후 출력된 값입니다.
- `paper_results/run2/` 는 같은 설정으로 다시 실행한 두 번째 실행의 원본 리포트 200개와 실행 기록입니다.
- `paper_results/rag_ablation/human_review_graded_by_claude.csv` 의 탐지 판정은 사람이 아니라 Claude가 응답을 읽고 한 번 판정한 결과입니다. 판정 기록은 `rag_ablation_graded_by_claude.py` 에 있습니다.
- `paper_results/latency/` 의 로컬 파일 경로는 `file:///<repo>/` 로 익명화했습니다.
- LLM 출력은 생성 온도 0에서도 실행마다 달라질 수 있어, 다시 실행하면 수치가 논문과 정확히 일치하지 않을 수 있습니다(논문의 반복 실행 결과 참고).
