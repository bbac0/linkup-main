"""
Pipeline Latency Measurement Script (v2)

v1 대비 변경 사항:
  1. RAG 검색 시 collection.query(query_texts=...) 대신, evaluate_rag_retrieval.py와
     동일하게 genai.embed_content로 Gemini 임베딩을 직접 계산한 뒤
     query_embeddings로 전달. (두 실험 간 임베딩 방식 불일치 제거)
  2. 측정 루프 시작 전 warm-up 호출(1회)을 명시적으로 수행하여 임베딩 함수/
     모델의 최초 로딩 비용(cold-start)이 평균에 섞여 들어가는 것을 방지.
     (논문 서술 "Cold-start를 제외한 Warm-state 기준"과 실제 코드를 일치시킴)
  3. 테스트 대상 URL을 연구 도메인(정부 UI/UX 가이드라인)과 무관한 사이트가
     아니라, Results 4.1의 50개 테스트베드 페이지 목록을 그대로 사용하도록 변경
     (파일에서 로드; 없으면 예시 placeholder 사용).
  4. 페이지당 1회가 아니라 N_REPEATS회 반복 측정 후 평균/표준편차를 함께 보고.
  5. 결과를 JSON으로 저장 (재현성/부록 자료).

주의:
  - TESTBED_URLS는 실제 50개 테스트베드 페이지 목록으로 교체해야 합니다.
    (Results 4.1에서 결함을 주입한 그 페이지들과 동일해야 두 실험 간
    일관성이 생깁니다.)
  - 컬렉션을 처음 구축할 때 사용한 embedding_function이 Gemini가 아니라면,
    이 스크립트에서 Gemini로 재계산한 쿼리 임베딩과 컬렉션에 저장된 문서
    임베딩의 공간이 어긋나 검색 품질 자체가 무효화될 수 있습니다.
    반드시 컬렉션 구축 스크립트를 확인하세요.
"""

import os
import time
import json
import statistics
import datetime
from typing import List, Dict, Any

import chromadb
import google.generativeai as genai
from playwright.sync_api import sync_playwright

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("환경변수 GEMINI_API_KEY 가 설정되어 있지 않습니다.")
genai.configure(api_key=GEMINI_API_KEY)

CHROMA_DB_PATH = os.environ.get("CHROMA_DB_PATH", "./services/chroma_db")
CHROMA_COLLECTION_NAME = os.environ.get("CHROMA_COLLECTION_NAME", "langchain")
EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBEDDING_DIM = 3072
GEN_MODEL_NAME = "gemini-2.5-flash"
N_RESULTS = 3
N_REPEATS = int(os.environ.get("N_REPEATS", "3"))  # 페이지당 반복 측정 횟수
PAGE_GOTO_TIMEOUT_MS = 20000

RESULTS_DIR = "results"
os.makedirs(RESULTS_DIR, exist_ok=True)

# Results 4.1의 50개 테스트베드 페이지 목록.
# 우선순위: (1) TESTBED_DIR 환경변수/기본 폴더에 로컬 HTML 파일이 있으면
# 그것을 자동 스캔, (2) testbed_urls.txt 파일이 있으면 그것을 사용,
# (3) 둘 다 없으면 placeholder 사용(측정에 쓰지 말 것).
import pathlib


def _load_local_testbed(dir_path: str) -> List[str]:
    """로컬 폴더 안의 page_XX.html 파일들을 file:// URI 목록으로 변환.

    Playwright는 원격 URL과 동일하게 file:// 경로도 page.goto()로 그대로
    열 수 있다. 정렬은 파일명이 page_01 ~ page_50 형태로 0-padding 되어
    있다는 전제 하에 문자열 정렬로도 올바른 순서가 나온다. 파일명 형식이
    다르면(page_1.html처럼 0-padding이 없으면) 정렬이 어긋나므로 주의.
    """
    folder = pathlib.Path(dir_path)
    if not folder.exists():
        return []
    html_files = sorted(folder.glob("*.html"))
    return [f.resolve().as_uri() for f in html_files]


TESTBED_DIR = os.environ.get("TESTBED_DIR", "./testbed")
_TESTBED_FILE = "testbed_urls.txt"

_local_urls = _load_local_testbed(TESTBED_DIR)
if _local_urls:
    TESTBED_URLS = _local_urls
    print(f"로컬 테스트베드 {len(TESTBED_URLS)}개 파일을 '{TESTBED_DIR}'에서 로드했습니다.")
elif os.path.exists(_TESTBED_FILE):
    with open(_TESTBED_FILE, encoding="utf-8") as f:
        TESTBED_URLS = [line.strip() for line in f if line.strip()]
else:
    TESTBED_URLS = [
        "https://plus.gov.kr/",
        # TODO: TESTBED_DIR 환경변수로 실제 testbed 폴더 경로를 지정하거나
        # testbed_urls.txt를 만들 것. 아래 두 개는 도메인이 다른 임시
        # placeholder이므로 논문용 측정에는 사용하지 말 것.
    ]


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------

def get_query_embedding(text: str) -> List[float]:
    response = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=text,
        task_type="retrieval_query",
        output_dimensionality=EMBEDDING_DIM,
    )
    embedding = response["embedding"]
    if len(embedding) != EMBEDDING_DIM:
        raise ValueError(
            f"임베딩 차원 불일치: 기대값 {EMBEDDING_DIM}, 실제 {len(embedding)}"
        )
    return embedding


def mean_std(values: List[float]) -> Dict[str, float]:
    if len(values) == 1:
        return {"mean": round(values[0], 3), "std": 0.0}
    return {
        "mean": round(statistics.mean(values), 3),
        "std": round(statistics.stdev(values), 3),
    }


# ---------------------------------------------------------------------------
# 측정 로직
# ---------------------------------------------------------------------------

def measure_pipeline_latency():
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    collection = client.get_or_create_collection(name=CHROMA_COLLECTION_NAME)
    gen_model = genai.GenerativeModel(GEN_MODEL_NAME)

    # --- Warm-up: cold-start 비용을 측정 루프 밖에서 미리 소모 ---
    print("Warm-up 실행 중 (cold-start 비용 제외 목적)...")
    _ = get_query_embedding("warm up query")
    _ = collection.query(
        query_embeddings=[get_query_embedding("warm up query")],
        n_results=1,
    )
    print("Warm-up 완료.\n")

    all_records: List[Dict[str, Any]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for url_idx, url in enumerate(TESTBED_URLS, 1):
            print(f"[{url_idx}/{len(TESTBED_URLS)}] {url}")

            for rep in range(1, N_REPEATS + 1):
                try:
                    # --- Data Acquisition (Playwright) ---
                    t0 = time.time()
                    page.goto(url, wait_until="networkidle",
                               timeout=PAGE_GOTO_TIMEOUT_MS)
                    screenshot_bytes = page.screenshot(type="png")
                    page_title = page.title() or url
                    t1 = time.time()
                    acquisition_time = t1 - t0

                    # --- RAG Vector Retrieval (Gemini Embedding + ChromaDB) ---
                    t2 = time.time()
                    query_text = (
                        f"UI layout guidelines and accessibility rules for {page_title}"
                    )
                    query_embedding = get_query_embedding(query_text)
                    rag_results = collection.query(
                        query_embeddings=[query_embedding],
                        n_results=N_RESULTS,
                    )
                    retrieved_docs = (
                        "\n".join(rag_results["documents"][0])
                        if rag_results.get("documents") else "No context"
                    )
                    t3 = time.time()
                    rag_time = t3 - t2

                    # --- Multimodal Inference ---
                    t4 = time.time()
                    prompt = f"Analyze this screenshot against guidelines: {retrieved_docs}"
                    response = gen_model.generate_content([
                        prompt,
                        {"mime_type": "image/png", "data": screenshot_bytes},
                    ])
                    _ = response.text
                    t5 = time.time()
                    inference_time = t5 - t4

                    total_time = acquisition_time + rag_time + inference_time

                    record = {
                        "url": url,
                        "repeat": rep,
                        "data_acquisition_sec": round(acquisition_time, 3),
                        "rag_retrieval_sec": round(rag_time, 3),
                        "llm_inference_sec": round(inference_time, 3),
                        "total_sec": round(total_time, 3),
                    }
                    all_records.append(record)
                    print(
                        f"  rep {rep}: total {record['total_sec']}s "
                        f"(수집 {record['data_acquisition_sec']}s | "
                        f"RAG {record['rag_retrieval_sec']}s | "
                        f"추론 {record['llm_inference_sec']}s)"
                    )
                except Exception as e:
                    print(f"  rep {rep}: 실패 ({e})")
                    all_records.append({
                        "url": url, "repeat": rep, "error": str(e)
                    })

        browser.close()

    valid_records = [r for r in all_records if "error" not in r]
    failed_count = len(all_records) - len(valid_records)

    summary = {
        "timestamp": datetime.datetime.now().isoformat(),
        "n_urls": len(TESTBED_URLS),
        "n_repeats_per_url": N_REPEATS,
        "n_valid_measurements": len(valid_records),
        "n_failed_measurements": failed_count,
        "data_acquisition_sec": mean_std(
            [r["data_acquisition_sec"] for r in valid_records]),
        "rag_retrieval_sec": mean_std(
            [r["rag_retrieval_sec"] for r in valid_records]),
        "llm_inference_sec": mean_std(
            [r["llm_inference_sec"] for r in valid_records]),
        "total_sec": mean_std([r["total_sec"] for r in valid_records]),
    }

    print("\n" + "=" * 60)
    print("     파이프라인 구간별 평균 소요 시간 (mean ± std)")
    print("=" * 60)
    print(f"측정 건수: {len(valid_records)} (실패 {failed_count}건)")
    print(f"1. 데이터 수집       : {summary['data_acquisition_sec']['mean']:.2f} "
          f"± {summary['data_acquisition_sec']['std']:.2f} 초")
    print(f"2. RAG 벡터 검색     : {summary['rag_retrieval_sec']['mean']:.3f} "
          f"± {summary['rag_retrieval_sec']['std']:.3f} 초")
    print(f"3. 멀티모달 추론     : {summary['llm_inference_sec']['mean']:.2f} "
          f"± {summary['llm_inference_sec']['std']:.2f} 초")
    print("-" * 60)
    print(f"전체 평균           : {summary['total_sec']['mean']:.2f} "
          f"± {summary['total_sec']['std']:.2f} 초 / 페이지")
    print("=" * 60)

    out_path = os.path.join(
        RESULTS_DIR,
        f"latency_eval_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "records": all_records},
                   f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장 완료: {out_path}")

    return summary


if __name__ == "__main__":
    measure_pipeline_latency()