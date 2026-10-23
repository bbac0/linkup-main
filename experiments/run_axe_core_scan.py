"""
axe-core 정적 분석 도구를 테스트베드 198페이지에 돌려, 본 연구의 4개
결함 카테고리(특히 Poor Contrast Ratio, Duplicate ID Markup Error)를
axe-core가 실제로 탐지하는지 확인하는 스크립트.

필요 패키지:
  pip install playwright --break-system-packages
  playwright install chromium

axe-core 스크립트는 로컬에 내려받아 사용한다 (CDN 의존 없이 재현 가능하도록):
  https://github.com/dequelabs/axe-core/releases 에서 axe.min.js 다운로드 후
  이 스크립트와 같은 폴더에 둘 것. (버전을 반드시 논문에 명시할 것 -
  예: "axe-core v4.X.X")

사용법:
  python run_axe_core_scan.py
"""

import os
import json
from playwright.sync_api import sync_playwright

TESTBED_DIR = "testbed_200"  # os.environ.get 대신 직접 지정
AXE_SCRIPT_PATH = os.environ.get("AXE_SCRIPT_PATH", "./axe.min.js")
METADATA_PATH = os.path.join(TESTBED_DIR, "dataset_metadata.json")
OUTPUT_PATH = "results/axe_core_scan.json"

# 본 연구의 5개 카테고리와 axe-core 룰을 어떻게 매핑할지는 직접 확인
# 필요. 아래는 "흔히 대응될 것으로 예상되는" 룰 ID 후보일 뿐이며,
# 실제 스캔 결과에 등장하는 ruleId를 보고 최종 확정해야 한다.
EXPECTED_RULE_CANDIDATES = {
    "Poor Contrast Ratio": ["color-contrast"],
    "Duplicate ID Markup Error": ["duplicate-id", "duplicate-id-active", "duplicate-id-aria"],
}


def load_axe_script() -> str:
    if not os.path.exists(AXE_SCRIPT_PATH):
        raise FileNotFoundError(
            f"{AXE_SCRIPT_PATH} 가 없습니다. axe-core 릴리스 페이지에서 "
            "axe.min.js를 내려받아 이 스크립트와 같은 폴더에 두세요: "
            "https://github.com/dequelabs/axe-core/releases"
        )
    with open(AXE_SCRIPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def main():
    import pathlib

    axe_js = load_axe_script()

    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        ground_truth_list = json.load(f)

    os.makedirs("results", exist_ok=True)
    all_results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for idx, gt in enumerate(ground_truth_list, 1):
            page_id = gt["page_id"].replace(".html", "")
            file_path = pathlib.Path(TESTBED_DIR, gt["page_id"]).resolve()
            if not file_path.exists():
                print(f"[{idx}] {page_id}: 파일 없음, 건너뜀")
                continue

            print(f"[{idx}/{len(ground_truth_list)}] axe-core 스캔: {page_id}")
            page.goto(file_path.as_uri())
            page.add_script_tag(content=axe_js)
            axe_result = page.evaluate("async () => await axe.run()")

            violations = axe_result.get("violations", [])
            rule_ids = [v["id"] for v in violations]

            all_results.append({
                "page_id": page_id,
                "has_gt_defect": gt["has_defect"],
                "gt_defect_types": [d["type"] for d in gt["defects"]],
                "axe_violation_rule_ids": rule_ids,
                "axe_violation_count": len(violations),
            })

        browser.close()

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    # --- 요약: 카테고리별로 axe-core가 실제로 뭔가를 잡았는지 집계 ---
    print("\n" + "=" * 60)
    print("axe-core 스캔 요약")
    print("=" * 60)

    for category, candidate_rules in EXPECTED_RULE_CANDIDATES.items():
        target_pages = [r for r in all_results if category in r["gt_defect_types"]]
        hit_count = 0
        for r in target_pages:
            if any(rid in r["axe_violation_rule_ids"] for rid in candidate_rules):
                hit_count += 1
        total = len(target_pages)
        rate = (hit_count / total * 100) if total else 0
        print(f"{category}: axe-core가 후보 룰({candidate_rules})로 탐지한 비율 "
              f"= {hit_count}/{total} ({rate:.1f}%)")

    # 어떤 룰들이 실제로 등장했는지 전체 집계 (후보 목록 검증용)
    all_rule_ids = set()
    for r in all_results:
        all_rule_ids.update(r["axe_violation_rule_ids"])
    print(f"\n스캔 전체에서 발견된 모든 axe-core 룰 ID: {sorted(all_rule_ids)}")
    print(f"\n상세 결과 저장됨: {OUTPUT_PATH}")
    print("\n[중요] EXPECTED_RULE_CANDIDATES는 추정치입니다. 위에 출력된 "
          "'전체 룰 ID' 목록을 보고 실제로 명도 대비/중복 ID에 대응하는 "
          "정확한 룰 ID를 확인한 뒤 결과를 재해석하세요.")


if __name__ == "__main__":
    main()