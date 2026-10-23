"""
RAG Ablation 실험: Standard Terminology Violation이 정답인 45개 페이지를
대상으로, (A) RAG 검색 컨텍스트를 포함한 프롬프트와 (B) RAG 컨텍스트를
완전히 제거한 프롬프트로 각각 분석을 돌려 결과를 비교한다.

이는 논문의 핵심 주장("RAG가 가이드라인 기반 결함 탐지에 기여한다")을
직접 입증하는 유일한 실험이다.

주의: cli.py의 실제 프롬프트 구성 로직을 그대로 재현할 수 없으므로,
이 스크립트는 "RAG 컨텍스트 삽입 여부를 제어할 수 있는 최소 골격"만
제공한다. 실제 cli.py가 RAG 컨텍스트를 어떤 함수/변수로 프롬프트에
주입하는지 확인하고, RAG_CONTEXT 값을 빈 문자열로 바꿔치기하는 지점만
정확히 맞추면 된다. 이 골격은 그 지점을 cli.py 대신 독립적으로
재현해 동일한 Gemini 호출을 두 번(RAG 있음/없음) 수행하는 방식이다.

사용법:
  python rag_ablation.py
"""

import os
import json
import time
import datetime
import random

import chromadb
import google.generativeai as genai
from playwright.sync_api import sync_playwright

try:
    from google.api_core import exceptions as google_exceptions
except ImportError:
    google_exceptions = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("환경변수 GEMINI_API_KEY 가 설정되어 있지 않습니다.")
genai.configure(api_key=GEMINI_API_KEY)

CHROMA_DB_PATH = os.environ.get("CHROMA_DB_PATH", "./services/chroma_db")
CHROMA_COLLECTION_NAME = os.environ.get("CHROMA_COLLECTION_NAME", "langchain")
EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBEDDING_DIM = 3072
GEN_MODEL_NAME = "gemini-2.5-flash"
TESTBED_DIR = "testbed_200"  # os.environ.get 대신 직접 지정
METADATA_PATH = os.path.join(TESTBED_DIR, "dataset_metadata.json")
RESULTS_DIR = "results/rag_ablation"

os.makedirs(RESULTS_DIR, exist_ok=True)


REQUEST_SLEEP_SEC = float(os.environ.get("RAG_ABLATION_SLEEP_SEC", "4"))
MAX_RETRIES = 6


def call_with_backoff(fn, *args, **kwargs):
    """429(rate limit) 등 일시적 오류에 지수 백오프로 재시도."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            is_resource_exhausted = (
                google_exceptions is not None
                and isinstance(e, google_exceptions.ResourceExhausted)
            )
            is_rate_limit = (
                is_resource_exhausted
                or "429" in str(e)
                or "rate" in str(e).lower()
                or "quota" in str(e).lower()
            )
            if not is_rate_limit or attempt == MAX_RETRIES:
                raise
            wait = min(60, (2 ** attempt)) + random.uniform(0, 1)
            print(f"  [429/rate limit] {attempt}/{MAX_RETRIES}회차 실패, "
                  f"{wait:.1f}초 대기 후 재시도... ({e})")
            time.sleep(wait)


def get_query_embedding(text: str):
    def _call():
        resp = genai.embed_content(
            model=EMBEDDING_MODEL, content=text,
            task_type="retrieval_query", output_dimensionality=EMBEDDING_DIM,
        )
        return resp["embedding"]
    return call_with_backoff(_call)


# !! 실제 cli.py의 프롬프트 템플릿을 그대로 옮겨 쓰세요. 아래는 최소 예시입니다.
# RAG 컨텍스트가 들어갈 자리에 {rag_context} 플레이스홀더를 두었습니다.
PROMPT_TEMPLATE = """다음 스크린샷을 분석하여 UI/UX 결함을 찾아주세요.

[검색된 가이드라인 컨텍스트]
{rag_context}

위 가이드라인을 참고하여, 특히 표준 용어 사용 여부를 포함해 결함을
JSON 형식으로 보고하세요. (issues 리스트에 category, description 포함)
"""


def analyze_page(screenshot_bytes: bytes, rag_context: str) -> dict:
    model = genai.GenerativeModel(GEN_MODEL_NAME)
    prompt = PROMPT_TEMPLATE.format(rag_context=rag_context if rag_context else "(RAG 컨텍스트 없음)")

    def _call():
        response = model.generate_content([
            prompt,
            {"mime_type": "image/png", "data": screenshot_bytes},
        ])
        return {"raw_text": response.text}

    return call_with_backoff(_call)


def get_rag_context(collection, query_text: str, n_results: int = 3) -> str:
    query_embedding = get_query_embedding(query_text)
    results = collection.query(query_embeddings=[query_embedding], n_results=n_results)
    docs = results.get("documents", [[]])[0]
    return "\n".join(docs) if docs else ""


def main():
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
    collection = client.get_collection(name=CHROMA_COLLECTION_NAME)

    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        ground_truth_list = json.load(f)

    target_pages = [
        gt for gt in ground_truth_list
        if any(d["type"] == "Standard Terminology Violation" for d in gt["defects"])
    ]
    print(f"대상 페이지: {len(target_pages)}개 (각 2회 호출 = 총 {len(target_pages)*2}회)")

    out_path = os.path.join(RESULTS_DIR, "ablation_checkpoint.json")

    all_results = []
    done_page_ids = set()
    if os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            all_results = json.load(f)
        done_page_ids = {r["page_id"] for r in all_results}
        print(f"[재개] 체크포인트에서 {len(done_page_ids)}개 페이지 완료 상태로 이어서 진행합니다.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for idx, gt in enumerate(target_pages, 1):
            page_id = gt["page_id"].replace(".html", "")

            if page_id in done_page_ids:
                print(f"[{idx}/{len(target_pages)}] {page_id}: 이미 완료됨, 건너뜀")
                continue

            import pathlib
            file_path = pathlib.Path(TESTBED_DIR, gt["page_id"]).resolve()
            if not file_path.exists():
                print(f"[{idx}] {page_id}: 파일 없음, 건너뜀")
                continue

            print(f"[{idx}/{len(target_pages)}] {page_id}")
            page.goto(file_path.as_uri())
            screenshot_bytes = page.screenshot(type="png")

            query_text = "UI layout guidelines and standard terminology rules"

            # --- (A) RAG 있음 ---
            rag_context = get_rag_context(collection, query_text)
            result_with_rag = analyze_page(screenshot_bytes, rag_context)
            time.sleep(REQUEST_SLEEP_SEC)

            # --- (B) RAG 없음 ---
            result_without_rag = analyze_page(screenshot_bytes, rag_context="")
            time.sleep(REQUEST_SLEEP_SEC)

            all_results.append({
                "page_id": page_id,
                "with_rag": result_with_rag,
                "without_rag": result_without_rag,
            })

            # 매 페이지마다 체크포인트 저장 - 중간에 끊겨도 처음부터 다시
            # 돌릴 필요 없이 이어서 진행 가능
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(all_results, f, ensure_ascii=False, indent=2)

        browser.close()

    final_path = os.path.join(
        RESULTS_DIR,
        f"ablation_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    with open(final_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\n완료. 결과 저장: {final_path} (체크포인트: {out_path})")
    print("\n다음 단계: 저장된 raw_text를 사람이 직접 읽고 각 조건에서 '용어 "
          "위반을 지적했는가(0/1)'를 채점하세요. RAG 있음/없음 탐지율을 "
          "비교하면 그게 바로 RAG 기여도의 직접 증거가 됩니다.")


if __name__ == "__main__":
    main()