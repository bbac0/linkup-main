"""
사용자가 제공한 45페이지 RAG ablation 원본 데이터를 Claude가 직접 읽고
채점한 결과. 채점 기준: 해당 응답이 그 페이지의 비표준 용어 레이블
(예: '접속하기', '접수하기', '제출하기' 등 페이지마다 다를 수 있음)을
명시적으로 지목하고 구체적인 대안 용어를 제시했는가 (1) 아닌가 (0).
단순히 "맥락이 불분명하다"는 수준의 서술은 0으로 처리.

이 스크립트는 그 채점 결과를 CSV로 저장해 기록을 남기기 위한 것이다.
"""

import csv
import os

# (page_id, with_rag_score, without_rag_score, cites_guideline_with, cites_guideline_without)
GRADES = [
    ("page_007", 1, 1, 1, 0),
    ("page_017", 0, 0, 0, 0),
    ("page_023", 0, 1, 1, 0),
    ("page_025", 1, 1, 1, 0),
    ("page_028", 1, 1, 1, 0),
    ("page_034", 1, 1, 1, 0),
    ("page_038", 1, 0, 1, 0),
    ("page_049", 1, 1, 1, 0),
    ("page_052", 0, 1, 1, 0),
    ("page_062", 1, 1, 1, 0),
    ("page_065", 0, 1, 1, 0),
    ("page_070", 1, 1, 1, 0),
    ("page_072", 0, 1, 0, 0),
    ("page_076", 1, 1, 1, 0),
    ("page_077", 1, 0, 1, 0),
    ("page_079", 1, 1, 1, 0),
    ("page_093", 0, 1, 1, 0),
    ("page_104", 1, 0, 1, 0),
    ("page_113", 0, 1, 1, 0),
    ("page_119", 1, 1, 1, 0),
    ("page_121", 0, 1, 1, 0),
    ("page_122", 1, 1, 1, 0),
    ("page_133", 0, 1, 1, 0),
    ("page_134", 0, 0, 1, 0),
    ("page_140", 1, 1, 1, 0),
    ("page_145", 0, 1, 1, 0),
    ("page_151", 1, 0, 1, 0),
    ("page_152", 1, 1, 1, 0),
    ("page_157", 0, 0, 1, 0),
    ("page_159", 1, 1, 1, 0),
    ("page_160", 1, 1, 1, 0),
    ("page_168", 1, 1, 1, 0),
    ("page_169", 1, 1, 1, 0),
    ("page_170", 1, 1, 1, 0),
    ("page_171", 0, 1, 1, 0),
    ("page_172", 0, 1, 1, 0),
    ("page_173", 0, 1, 1, 0),
    ("page_174", 0, 1, 1, 0),
    ("page_175", 0, 1, 1, 0),
    ("page_177", 1, 1, 1, 0),
    ("page_179", 1, 1, 1, 0),
    ("page_180", 1, 1, 1, 0),
    ("page_181", 1, 1, 1, 0),
    ("page_193", 0, 1, 1, 0),
    ("page_200", 1, 1, 1, 0),
]

BORDERLINE_NOTES = {
    "page_017": "without_rag가 '접수하기' 맥락 불분명을 언급하나 구체적 대안 용어 미제시 -> 0",
    "page_023": "with_rag는 '아이디' 축약어만 지적, 'with_rag' 자체의 제목인 '접속하기'는 언급 안 함 -> 0",
    "page_038": "without_rag는 '성명' vs '이름' 용어를 지적하나, 실제 타겟으로 추정되는 '제출하기' 버튼은 언급 안 함 -> 0",
    "page_052": "with_rag가 'note_on_terminology'에서 명시적으로 '결함 없음'이라 서술 -> 0",
    "page_077": "without_rag는 '접수하기' 언급하나 구체적 대안 제시 없이 맥락만 요구 -> 0",
    "page_104": "without_rag는 '접수하기'를 스쳐 언급하나 breadcrumb 부재 논의가 중심, 대안 용어 미제시 -> 0",
    "page_121": "두 조건 모두 '배너' 레이블의 일관성 문제를 지적; with_rag는 구체적 대안 없이 '일관성 부족'만 언급 -> 0, without_rag는 '배너 관리/생성' 대안 제시 -> 1",
    "page_151": "without_rag는 '접수하기' vs '신청하기' 비교만 하고 구체적 대안 미제시 -> 0",
    "page_171": "with_rag는 '접수하기'/'배너' 모호성만 언급, 구체적 대안 미제시 -> 0",
    "page_173": "with_rag는 '아이디'에 설명(툴팁) 추가를 권고할 뿐 대안 용어 제시는 안 함 -> 0",
}


def main():
    os.makedirs("results/rag_ablation", exist_ok=True)
    out_path = "results/rag_ablation/human_review_graded_by_claude.csv"

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "page_id", "with_rag_detected", "without_rag_detected",
            "with_rag_cites_guideline", "without_rag_cites_guideline", "note",
        ])
        for page_id, w, wo, cw, cwo in GRADES:
            writer.writerow([page_id, w, wo, cw, cwo, BORDERLINE_NOTES.get(page_id, "")])

    n = len(GRADES)
    with_detect = sum(g[1] for g in GRADES)
    without_detect = sum(g[2] for g in GRADES)
    both = sum(1 for g in GRADES if g[1] == 1 and g[2] == 1)
    neither = sum(1 for g in GRADES if g[1] == 0 and g[2] == 0)
    only_with = sum(1 for g in GRADES if g[1] == 1 and g[2] == 0)
    only_without = sum(1 for g in GRADES if g[1] == 0 and g[2] == 1)
    with_cite = sum(g[3] for g in GRADES)
    without_cite = sum(g[4] for g in GRADES)

    print(f"N = {n}")
    print(f"RAG 있음 탐지율  : {with_detect}/{n} = {with_detect/n*100:.1f}%")
    print(f"RAG 없음 탐지율  : {without_detect}/{n} = {without_detect/n*100:.1f}%")
    print(f"둘 다 탐지 / 둘 다 미탐 / RAG만 탐지 / RAG없음만 탐지 = "
          f"{both} / {neither} / {only_with} / {only_without}")
    print(f"RAG 있음 가이드라인 인용률: {with_cite}/{n} = {with_cite/n*100:.1f}%")
    print(f"RAG 없음 가이드라인 인용률: {without_cite}/{n} = {without_cite/n*100:.1f}%")
    print(f"\n저장됨: {out_path}")


if __name__ == "__main__":
    main()
