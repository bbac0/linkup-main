"""
프로덕션(analyzer.py)이 모든 페이지에 사용하는 고정 검색 질의가 실제로 어떤 청크를
가져오는지 출력한다. 표준 용어 위반 탐지가 낮았던 원인이 '관련 가이드라인이 프롬프트에
들어가지 않아서'인지 확인하는 용도다.

사용법 (프로젝트 루트, 즉 ./chroma_db 가 있는 폴더에서 실행)
  python show_fixed_retrieval.py

환경변수: GEMINI_API_KEY (또는 GOOGLE_API_KEY)
"""

import os
import sys

QUERY = "웹 UI/UX 표준 가이드라인 공통 규정"   # analyzer.py 와 동일
K = 4
PROBE_TERMS = ["로그인", "접속", "용어", "레이블", "명도", "대비", "id", "중복", "autocomplete", "반응형"]


def main():
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        sys.exit("GEMINI_API_KEY 또는 GOOGLE_API_KEY 환경변수가 필요합니다.")
    os.environ["GOOGLE_API_KEY"] = key

    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    from langchain_chroma import Chroma

    embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")
    store = Chroma(persist_directory="./chroma_db", embedding_function=embeddings)

    # 거리 함수와 청크 수 확인
    try:
        col = store._collection
        print(f"컬렉션 이름: {col.name} / 청크 수: {col.count()} / 메타데이터: {col.metadata}")
        print("  (메타데이터에 hnsw:space 가 없으면 ChromaDB 기본값인 L2(제곱 유클리드) 거리를 쓴다)\n")
    except Exception as e:
        print(f"컬렉션 정보를 읽지 못했습니다: {e}\n")

    docs_scores = store.similarity_search_with_score(QUERY, k=K)
    print(f"질의: {QUERY!r}, K={K}\n")
    union_text = ""
    for rank, (doc, score) in enumerate(docs_scores, 1):
        src = os.path.basename(doc.metadata.get("source", "?"))
        page = doc.metadata.get("page", 0) + 1
        print(f"[{rank}] {src} - {page}페이지  (거리 {score:.4f})")
        print("    " + doc.page_content.replace("\n", " ")[:300] + " ...\n")
        union_text += doc.page_content

    print("검색된 4개 청크에 아래 용어가 포함되어 있는가:")
    for term in PROBE_TERMS:
        print(f"  {term:12s}: {'있음' if term.lower() in union_text.lower() else '없음'}")
    print("\n해석: '로그인'/'접속'/'용어'가 없다면, 프로덕션 프롬프트에는 표준 용어를 판정할 근거가 "
          "들어 있지 않은 것이다. 고정 질의가 모든 페이지에 같은 4개 청크만 제공하기 때문이다.")


if __name__ == "__main__":
    main()