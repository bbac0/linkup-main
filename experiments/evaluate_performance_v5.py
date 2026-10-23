"""
200페이지 채점 스크립트 (v4) - 크로스 페이지 오염 필터링 추가판

v3 대비 변경 사항:
  1. [핵심 수정] 크롤러가 대상 페이지 외에 링크로 연결된 다른 페이지
     (예: /login 에러 페이지)까지 함께 분석해서 report의 "issues"에
     섞어 넣는 것을 확인했다. 이 issue들은 description 맨 앞에
     "[URL]" 형태로 대상 URL이 명시되어 있으므로, 지금 채점 중인
     테스트베드 페이지 자신의 URL을 포함하지 않는 issue는 카테고리
     매칭 이전에 아예 제외한다 (다른 페이지에 대한 지적이 현재 페이지의
     결함 탐지 여부에 영향을 주지 않도록).
  2. Button Area Clipping 매칭 구문 보강: 실제 모델 출력을 확인한 결과
     "버튼 잘림", "clipping" 같은 표현이 아니라 "클릭 영역", "클릭할 수
     없", "상호작용이 불가능", "시각적으로 거의 보이지", 그리고 주입된
     CSS 값 자체("font-size: 0px", "height: 2px")로 표현되고 있었다.
  3. "정답 카테고리와 무관한 issue"를 "같은 페이지인데 무관한 issue"와
     "아예 다른 페이지(크롤링된 링크)에 대한 issue"로 분리 집계하여,
     이 둘을 구분해서 볼 수 있게 했다.

주의: Standard Terminology Violation은 아직 실제 표현을 확인하지 못했다.
inspect_unmatched_defects.py "Standard Terminology Violation" 실행 결과를
보고 phrase를 추가로 보강해야 한다.

실제 리포트 스키마 (사용자 제공 샘플 기준):
{
  "status": "success",
  "total_count": ...,
  "design_faults": ..., "ops_faults": ..., "tech_faults": ...,
  "summary": "자유 서술형 텍스트 - 절대 결함 판정에 사용하지 말 것! "
              "'결함이 없습니다' 류의 부정문에도 '결함'이라는 단어가 그대로 "
              "들어있어 v1과 같은 오탐 버그가 재발한다.",
  "issues": [
    {"category": "운영"|"기술"|"디자인", "description": "...", "suggestion": "...",
     "source_file": "...", "page_number": [...]}
  ],
  "scores": {"디자인": int, "운영": int, "기술": int},
  "tree_nodes": [...]
}

v2 대비 변경 사항:
  1. "issues" 리스트가 실제 필드임을 확인. 다만 len(issues) > 0 을 그대로
     "결함 있음" 판정에 쓰면 안 된다 - 테스트베드 공통 보일러플레이트
     ("테스트용 페이지" 배너, 고정된 <title>)가 모든 페이지에서 동일하게
     지적될 가능성이 높아, 실제 주입된 결함과 무관하게 거의 모든 페이지가
     "issues 존재"로 잡혀 다시 전부-양성 문제가 재발할 수 있기 때문이다.
     -> has_detected_defect 는 "issues 중 5개 정답 카테고리와 매칭되는
     항목이 있는가"로 재정의했다.
  2. 결함 유형 매칭은 issue의 "description" 필드에서만 수행 (summary나
     suggestion, category 필드는 사용하지 않음 - summary는 부정문 포함
     가능성이 있고, category는 디자인/운영/기술 3분류라 5개 세부 카테고리와
     1:1 매칭되지 않는 상위 분류이기 때문).
  3. 5개 정답 카테고리에 해당하지 않는 issue(보일러플레이트 등)는
     "unrelated_issue"로 별도 집계하여, 테스트베드 자체의 문제인지
     확인할 수 있게 했다.
  4. run_manifest.json의 실패 페이지 목록을 읽어와 해당 페이지는
     TP/FP/FN/TN 계산에서 완전히 제외하고 "미평가"로 별도 보고한다.
     (파이프라인 실행 실패를 모델의 탐지 실패로 오인하지 않기 위함)
"""

import os
import json

METADATA_PATH = os.path.join("testbed_200", "dataset_metadata.json")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results")
MANIFEST_PATH = os.path.join(RESULTS_DIR, "run_manifest.json")

# 5개 정답 카테고리 판정을 위한 description 매칭 구문.
# 실제 issue의 "description" 텍스트 안에서만 검색한다 (전체 JSON 아님).
CATEGORY_PHRASES = {
    "Poor Contrast Ratio": [
        "명도 대비", "대비 비율", "contrast ratio", "wcag", "가독성",
    ],
    "Mobile Layout Breakage": [
        "레이아웃이 깨", "레이아웃 깨짐", "가로 스크롤", "viewport",
        "모바일 화면", "반응형", "overflow",
    ],
    "Button Area Clipping": [
        "버튼이 잘", "버튼 잘림", "버튼 영역", "clipping", "겹침",
        "overlap", "클릭 영역", "클릭할 수 없", "클릭할수 없",
        "상호작용이 불가능", "시각적으로 거의 보이지",
        "font-size: 0px", "height: 2px",
    ],
    "Standard Terminology Violation": [
        "표준 용어", "용어 위반", "terminology", "용어 불일치",
        "용어가 표준", "접속하기",
    ],
    "Duplicate ID Markup Error": [
        "id가 중복", "중복된 id", "duplicate id", "동일한 id",
        "id 속성이 중복", "wai-aria", "두 번 사용되어", "중복 사용",
    ],
}


def load_failed_pages() -> set:
    """run_batch_v2.py가 남긴 manifest에서 최종 실패 페이지 번호를 읽어온다."""
    if not os.path.exists(MANIFEST_PATH):
        return set()
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        failed = manifest.get("failed_pages", [])
        return {f"page_{i:03d}" for i in failed}
    except Exception:
        return set()


def match_categories_in_issue(description: str) -> set:
    """하나의 issue description에서 매칭되는 정답 카테고리 집합을 반환."""
    desc_lower = description.lower()
    matched = set()
    for category, phrases in CATEGORY_PHRASES.items():
        for phrase in phrases:
            if phrase.lower() in desc_lower:
                matched.add(category)
                break
    return matched


def evaluate_performance():
    if not os.path.exists(METADATA_PATH):
        print(f"[실패] 정답지 파일({METADATA_PATH})을 찾을 수 없습니다.")
        return

    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        ground_truth_list = json.load(f)

    failed_pages = load_failed_pages()
    if failed_pages:
        print(f"[안내] run_manifest.json 기준 실행 실패 페이지 {len(failed_pages)}개"
              f"({sorted(failed_pages)})는 평가에서 제외합니다.\n")

    tp, fp, fn, tn = 0, 0, 0, 0
    excluded_count = 0
    total_unrelated_issues = 0
    total_off_target_issues = 0

    defect_stats = {k: {"gt": 0, "detected": 0} for k in CATEGORY_PHRASES}

    for gt in ground_truth_list:
        page_id = gt["page_id"].replace(".html", "")

        if page_id in failed_pages:
            excluded_count += 1
            continue

        report_file = os.path.join(RESULTS_DIR, f"report_{page_id}.json")

        has_gt_defect = gt["has_defect"]
        gt_defects = {d["type"] for d in gt["defects"]}

        for d_type in gt_defects:
            if d_type in defect_stats:
                defect_stats[d_type]["gt"] += 1

        if not os.path.exists(report_file):
            # 실행은 됐다고 기록됐는데 파일이 없는 경우 -> 이것도 진짜
            # "탐지 실패"가 아니라 "결과 파일 누락"이므로 별도 카운트.
            excluded_count += 1
            print(f"[경고] {page_id}: manifest에는 성공으로 기록되었으나 "
                  f"리포트 파일이 없습니다. 평가에서 제외합니다.")
            continue

        try:
            with open(report_file, "r", encoding="utf-8") as f:
                report_data = json.load(f)
        except Exception as e:
            excluded_count += 1
            print(f"[경고] {page_id}: 리포트 JSON 파싱 실패({e}). 제외합니다.")
            continue

        issues = report_data.get("issues", [])
        if not isinstance(issues, list):
            issues = []

        # 크롤러가 링크를 따라가 분석한 다른 페이지(예: /login)의 issue가
        # 섞여 들어오는 것을 방지하기 위해, description에 현재 채점 중인
        # 테스트베드 페이지 자신의 URL이 포함된 issue만 "대상 페이지 issue"로
        # 인정한다. 판별 문자열은 파일명 기준으로 최대한 느슨하게 잡는다
        # (호스트/포트가 바뀌어도 걸리도록).
        page_url_marker = f"testbed_200/{page_id}.html"

        on_target_issues = []
        off_target_count_this_page = 0
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            description = issue.get("description", "")
            if not isinstance(description, str):
                description = ""
            if page_url_marker in description:
                on_target_issues.append(issue)
            else:
                off_target_count_this_page += 1

        total_off_target_issues += off_target_count_this_page

        # description 필드에서만 카테고리 매칭 수행 (summary는 절대 사용하지 않음)
        matched_types_this_page = set()
        unrelated_count_this_page = 0
        for issue in on_target_issues:
            description = issue.get("description", "")
            if not isinstance(description, str):
                description = ""
            matched = match_categories_in_issue(description)
            if matched:
                matched_types_this_page |= matched
            else:
                unrelated_count_this_page += 1

        total_unrelated_issues += unrelated_count_this_page

        # binary 판정: 5개 정답 카테고리 중 하나라도 매칭된 issue가 있는가
        has_detected_defect = len(matched_types_this_page) > 0

        if has_gt_defect and has_detected_defect:
            tp += 1
        elif not has_gt_defect and has_detected_defect:
            fp += 1
        elif has_gt_defect and not has_detected_defect:
            fn += 1
        else:
            tn += 1

        for d_type in gt_defects:
            if d_type in matched_types_this_page:
                defect_stats[d_type]["detected"] += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0

    evaluated_total = tp + fp + fn + tn

    print("=" * 60)
    print("[200개 테스트베드 종합 평가 결과 리포트 (v5 - 용어위반/중복ID 문구 보강)]")
    print("=" * 60)
    print(f"전체 페이지: {len(ground_truth_list)}개 | 평가 제외: {excluded_count}개 "
          f"| 실제 평가: {evaluated_total}개")
    print(f"  (결함 페이지: {tp+fn}개, 정상 대조군: {fp+tn}개)")
    print(f"대상 페이지 외 다른 URL(크롤링된 링크 등)에 대한 issue 총 "
          f"{total_off_target_issues}건 -> 채점에서 제외됨")
    print(f"대상 페이지 issue 중 정답 카테고리와 무관한 issue 총 "
          f"{total_unrelated_issues}건 발견")
    print("-" * 60)
    print(f"Precision (정밀도) : {precision:.4f} ({tp}/{tp+fp})")
    print(f"Recall    (재현율) : {recall:.4f} ({tp}/{tp+fn})")
    print(f"F1-Score  (F1 점수): {f1_score:.4f}")
    print("-" * 60)
    print("세부 결함 유형별 탐지율:")
    for d_type, stat in defect_stats.items():
        rate = (stat["detected"] / stat["gt"] * 100) if stat["gt"] > 0 else 0
        print(f"  - {d_type:32s}: {rate:6.2f}% ({stat['detected']}/{stat['gt']})")
    print("=" * 60)
    print("\n주의: CATEGORY_PHRASES는 샘플 1건만 보고 작성한 초안입니다. "
          "'정답 카테고리와 무관한 issue' 수가 많거나, 특정 카테고리의 gt "
          "수치가 0이면 매칭 구문이 실제 모델 응답 표현과 어긋났을 수 있으니 "
          "dataset_metadata.json의 실제 defect 표기와 report의 description "
          "표현을 다시 대조해보시기 바랍니다.")


if __name__ == "__main__":
    evaluate_performance()