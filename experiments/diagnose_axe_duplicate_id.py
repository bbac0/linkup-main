"""
axe-core에서 duplicate-id 계열 룰이 왜 한 번도 발견되지 않았는지 진단한다.

1. axe.getRules()로 이 axe-core 버전에 duplicate-id 관련 룰이 실제로
   존재하는지, 존재한다면 태그(tags)가 무엇인지 확인한다 (best-practice로
   분류되어 기본 실행에서 빠졌을 가능성 확인).
2. 중복 id가 실제로 존재한다고 알려진 페이지(1개) 하나를 골라
   axe.run()의 '전체' 결과(violations뿐 아니라 passes, incomplete,
   inapplicable까지)를 덤프해서, 그 룰이 거기 어디에도 전혀 안 나타나는지,
   아니면 'pass'나 'inapplicable'로는 나타나는지 확인한다.

사용법:
  python diagnose_axe_duplicate_id.py
"""

import os
import json
import pathlib
from playwright.sync_api import sync_playwright

TESTBED_DIR = "testbed_200"  # os.environ.get 대신 직접 지정
AXE_SCRIPT_PATH = os.environ.get("AXE_SCRIPT_PATH", "./axe.min.js")

# 이전 대화에서 실제로 id="main-banner"가 중복된다고 모델이 지적했던 페이지
KNOWN_DUPLICATE_ID_PAGE = os.environ.get("KNOWN_DUPLICATE_ID_PAGE", "page_065.html")


def load_axe_script() -> str:
    with open(AXE_SCRIPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def main():
    axe_js = load_axe_script()
    file_path = pathlib.Path(TESTBED_DIR, KNOWN_DUPLICATE_ID_PAGE).resolve()

    if not file_path.exists():
        print(f"[오류] {file_path} 가 없습니다. KNOWN_DUPLICATE_ID_PAGE를 "
              f"실제로 중복 id가 있는 페이지로 지정하세요.")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(file_path.as_uri())
        page.add_script_tag(content=axe_js)

        # --- 1. 이 axe-core 버전에 duplicate-id 관련 룰이 있는지, 태그가 뭔지 ---
        all_rules = page.evaluate("() => axe.getRules()")
        id_related_rules = [
            r for r in all_rules
            if "id" in r.get("ruleId", "").lower() or "duplicate" in r.get("ruleId", "").lower()
        ]

        print("=" * 70)
        print("[1] 이 axe-core 버전에 존재하는 'id' 관련 룰 전체")
        print("=" * 70)
        if not id_related_rules:
            print("id 관련 룰이 axe.getRules() 목록에 단 하나도 없습니다 - "
                  "이 axe-core 빌드/버전 자체에 duplicate-id 룰이 없다는 뜻입니다.")
        for r in id_related_rules:
            print(json.dumps(r, ensure_ascii=False, indent=2))

        # --- 2. 중복 id가 실제로 있는 페이지에 대해 전체 결과 덤프 ---
        full_result = page.evaluate("""async () => {
            const result = await axe.run(document, {
                runOnly: undefined  // 특정 태그로 제한하지 않고 전체 룰 실행
            });
            return result;
        }""")

        print("\n" + "=" * 70)
        print(f"[2] {KNOWN_DUPLICATE_ID_PAGE} 전체 axe.run() 결과에서 "
              "'id' 문자열이 포함된 모든 항목")
        print("=" * 70)

        found_anything = False
        for section in ["violations", "passes", "incomplete", "inapplicable"]:
            items = full_result.get(section, [])
            matching = [i for i in items if "id" in i.get("id", "").lower()]
            if matching:
                found_anything = True
                print(f"\n--- {section} ({len(matching)}건) ---")
                for m in matching:
                    print(f"  ruleId: {m.get('id')}, tags: {m.get('tags')}, "
                          f"description: {m.get('description')}")

        if not found_anything:
            print("violations/passes/incomplete/inapplicable 어디에도 "
                  "id 관련 룰이 전혀 나타나지 않았습니다. 이 axe-core 버전에는 "
                  "duplicate-id 계열 룰이 아예 번들링되어 있지 않을 가능성이 "
                  "높습니다 (가벼운 locale/minimal 빌드를 받았을 수도 있음 - "
                  "공식 npm 패키지의 axe.min.js로 다시 받아보는 것을 권장).")

        # --- 3. 실제 HTML에서 id 중복이 맞는지 Python으로도 직접 확인 ---
        html = file_path.read_text(encoding="utf-8")
        import re
        id_matches = re.findall(r'id="([^"]+)"', html)
        dupes = [x for x in set(id_matches) if id_matches.count(x) > 1]
        print(f"\n[3] 해당 HTML 파일을 직접 정규식으로 확인한 결과: "
              f"id 중복 = {dupes if dupes else '없음'}")

        browser.close()


if __name__ == "__main__":
    main()