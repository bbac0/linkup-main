"""
입력 모달리티 ablation: 스크린샷만 / HTML 소스만 / 둘 다.

목적
  현재 시스템은 스크린샷과 HTML 소스를 함께 모델에 준다. 리포트가 CSS 값
  (예: width: 3000px, height: 2px)을 직접 인용하므로, 결함 탐지가 "화면을
  본 것"인지 "소스를 읽은 것"인지 구분되지 않는다. 이 스크립트는 같은 페이지를
  세 가지 입력 조건으로 분석하여 그 기여를 분리한다.

사용법 (대괄호 없이 그대로 입력)
  python modality_ablation.py run
  python modality_ablation.py run --per-category 6 --controls 6   # 표본을 줄이고 싶을 때만
  python modality_ablation.py score

설계 메모
  - 세 조건은 프롬프트 지시문이 같고, 제공 자료만 다르다.
  - RAG 컨텍스트는 모든 조건에서 제외한다 (모달리티 효과만 분리하기 위해).
  - 아래 BASE_PROMPT는 일반적인 검수 프롬프트다. 프로덕션(cli.py) 프롬프트와
    맞추려면 BASE_PROMPT를 그대로 교체할 것. 단, <<INPUT_NOTE>> 자리는 유지.
  - 채점은 evaluate_performance_v5.py와 같은 키워드 사전을 쓴다. 키워드 채점의
    한계는 그대로 적용되므로, 세 조건을 같은 기준으로 비교하는 용도로만 해석할 것.
"""

import os
import re
import sys
import json
import math
import time
import random
import argparse
import pathlib

TESTBED_DIR = "testbed_200"  # os.environ.get 대신 직접 지정
METADATA_PATH = os.path.join(TESTBED_DIR, "dataset_metadata.json")
MANIFEST_PATH = os.path.join("results", "run_manifest.json")
OUT_DIR = os.path.join("results", os.environ.get("MODALITY_DIR", "modality_ablation"))
SAMPLE_PATH = os.path.join(OUT_DIR, "sample.json")
RAW_PATH = os.path.join(OUT_DIR, "raw_outputs.json")
SHOT_DIR = os.path.join(OUT_DIR, "shots")
SUMMARY_PATH = os.path.join(OUT_DIR, "summary.json")

MODEL_NAME = os.environ.get("GEN_MODEL", "gemini-2.5-flash")
# PRODUCTION_MATCH=1 이면 analyzer.py 와 같은 입력을 만든다:
#   뷰포트 1280x800, 전체 페이지가 아닌 뷰포트 스크린샷, networkidle 후 3초 대기,
#   HTML 은 렌더링 후 DOM(page.content())의 앞 5,000자.
# 기본값(0)은 이전 설정(1280x720, 전체 페이지 스크린샷, 원본 파일 전체)이다.
PRODUCTION_MATCH = os.environ.get("PRODUCTION_MATCH") == "1"
SLEEP_SEC = float(os.environ.get("SLEEP_SEC", "4"))
MAX_RETRIES = 6

CONDITIONS = ["both", "image_only", "html_only"]
DEFECT_TYPES = [
    "Poor Contrast Ratio",
    "Mobile Layout Breakage",
    "Button Area Clipping",
    "Standard Terminology Violation",
    "Duplicate ID Markup Error",
]

# evaluate_performance_v5.py와 동일한 사전 (비교 가능성 유지)
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

BASE_PROMPT = """당신은 웹 UI/UX 품질 검수 전문가입니다. 제공된 자료를 분석하여 사용자 경험, 접근성, 용어 사용, 마크업 품질 측면의 결함을 찾아 주세요.

<<INPUT_NOTE>>

반드시 아래 JSON 형식으로만 답하세요. 결함이 없으면 issues를 빈 배열로 두세요.
{"issues": [{"category": "디자인 또는 운영 또는 기술", "description": "결함 설명", "suggestion": "개선 제안"}]}
"""

INPUT_NOTES = {
    "both": "제공 자료: (1) 페이지 스크린샷, (2) 페이지의 HTML 소스.",
    "image_only": "제공 자료: 페이지 스크린샷만 제공됩니다. HTML 소스는 제공되지 않습니다.",
    "html_only": "제공 자료: 페이지의 HTML 소스만 제공됩니다. 렌더링된 화면은 제공되지 않습니다.",
}


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------

def wilson(successes, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    d = 1 + z ** 2 / n
    c = p + z ** 2 / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2))
    return (max(0.0, (c - m) / d), min(1.0, (c + m) / d))


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = 2 * sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, p)


def pid(entry):
    return entry["page_id"].replace(".html", "")


def load_meta():
    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_failed_pages():
    if not os.path.exists(MANIFEST_PATH):
        return set()
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            m = json.load(f)
        return {f"page_{i:03d}" for i in m.get("failed_pages", [])}
    except Exception:
        return set()


def match_categories(text):
    low = text.lower()
    out = set()
    for cat, phrases in CATEGORY_PHRASES.items():
        if any(p.lower() in low for p in phrases):
            out.add(cat)
    return out


def parse_descriptions(raw_text):
    """모델 응답에서 issue 설명문 목록을 추출. 파싱 실패 시 원문 전체를 1건으로 취급."""
    text = (raw_text or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        i, j = text.find("{"), text.rfind("}")
        if i != -1 and j > i:
            text = text[i:j + 1]
    try:
        data = json.loads(text)
        issues = data.get("issues", [])
        return [str(x.get("description", "")) for x in issues if isinstance(x, dict)], True
    except Exception:
        return [raw_text or ""], False


# ---------------------------------------------------------------------------
# 표본 구성
# ---------------------------------------------------------------------------

def build_sample(per_category, controls, seed):
    meta = load_meta()
    failed = load_failed_pages()
    rng = random.Random(seed)
    chosen = {}

    for cat in DEFECT_TYPES:
        pool = sorted(
            [g for g in meta
             if any(d["type"] == cat for d in g["defects"]) and pid(g) not in failed],
            key=lambda g: g["page_id"])
        for g in rng.sample(pool, min(per_category, len(pool))):
            chosen[pid(g)] = g

    ctrl_pool = sorted(
        [g for g in meta if not g["has_defect"] and pid(g) not in failed],
        key=lambda g: g["page_id"])
    for g in rng.sample(ctrl_pool, min(controls, len(ctrl_pool))):
        chosen[pid(g)] = g

    return [
        {"page_id": pid(g), "html_file": g["page_id"],
         "has_defect": g["has_defect"],
         "gt_types": sorted({d["type"] for d in g["defects"]})}
        for _, g in sorted(chosen.items())
    ]


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def call_with_backoff(fn):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:
            msg = str(e).lower()
            retriable = any(k in msg for k in ("429", "rate", "quota", "503", "unavailable"))
            if not retriable or attempt == MAX_RETRIES:
                raise
            wait = min(60, 2 ** attempt) + random.uniform(0, 1)
            print(f"  [재시도 {attempt}/{MAX_RETRIES}] {wait:.1f}초 대기: {e}")
            time.sleep(wait)


def run_cmd(args):
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"[저장 위치] {os.path.abspath(OUT_DIR)}  (score도 같은 폴더에서 실행해야 합니다)")
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        sys.exit("환경변수 GEMINI_API_KEY가 필요합니다.")

    import google.generativeai as genai
    from playwright.sync_api import sync_playwright

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(MODEL_NAME)

    os.makedirs(SHOT_DIR, exist_ok=True)
    if os.path.exists(SAMPLE_PATH) and not args.resample:
        with open(SAMPLE_PATH, "r", encoding="utf-8") as f:
            sample = json.load(f)
        print(f"[표본] 기존 sample.json 사용 ({len(sample)}개 페이지)")
    else:
        sample = build_sample(args.per_category, args.controls, args.seed)
        with open(SAMPLE_PATH, "w", encoding="utf-8") as f:
            json.dump(sample, f, ensure_ascii=False, indent=2)
        print(f"[표본] 새로 구성: {len(sample)}개 페이지 x {len(CONDITIONS)}조건 "
              f"= {len(sample) * len(CONDITIONS)}회 호출")

    raw = {}
    if os.path.exists(RAW_PATH):
        with open(RAW_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        print(f"[재개] 완료된 호출 {len(raw)}건")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800 if PRODUCTION_MATCH else 720})
        print(f"[설정] PRODUCTION_MATCH={PRODUCTION_MATCH}, 출력 폴더={OUT_DIR}")

        for idx, item in enumerate(sample, 1):
            page_id = item["page_id"]
            html_path = pathlib.Path(TESTBED_DIR, item["html_file"]).resolve()
            if not html_path.exists():
                print(f"[{idx}] {page_id}: 파일 없음, 건너뜀")
                continue

            shot_path = os.path.join(SHOT_DIR, f"{page_id}.png")
            need_shot = not os.path.exists(shot_path)
            if PRODUCTION_MATCH or need_shot:
                page.goto(html_path.as_uri(), wait_until="networkidle" if PRODUCTION_MATCH else "load")
                if PRODUCTION_MATCH:
                    page.wait_for_timeout(3000)
            if need_shot:
                page.screenshot(path=shot_path, full_page=not PRODUCTION_MATCH)
            with open(shot_path, "rb") as f:
                shot_bytes = f.read()
            html_text = (page.content()[:5000] if PRODUCTION_MATCH
                         else html_path.read_text(encoding="utf-8"))

            for cond in CONDITIONS:
                key = f"{page_id}|{cond}"
                if key in raw:
                    continue
                prompt = BASE_PROMPT.replace("<<INPUT_NOTE>>", INPUT_NOTES[cond])
                if cond in ("both", "html_only"):
                    prompt += f"\n[HTML 소스]\n```html\n{html_text}\n```\n"
                parts = [prompt]
                if cond in ("both", "image_only"):
                    parts.append({"mime_type": "image/png", "data": shot_bytes})

                print(f"[{idx}/{len(sample)}] {page_id} / {cond}")
                text = call_with_backoff(lambda: model.generate_content(parts).text)
                raw[key] = {"page_id": page_id, "condition": cond, "raw_text": text}
                with open(RAW_PATH, "w", encoding="utf-8") as f:
                    json.dump(raw, f, ensure_ascii=False, indent=2)
                time.sleep(SLEEP_SEC)

        browser.close()
    print(f"\n완료. 다음 단계: python modality_ablation.py score")


# ---------------------------------------------------------------------------
# 채점
# ---------------------------------------------------------------------------

def compute_scores(sample, raw, describe=None):
    """sample: 표본 목록, raw: {'page|cond': {...}} -> 요약 dict 반환.
    describe: 설명문 1개를 받아 유형 집합을 돌려주는 함수 (기본: 키워드 매칭)."""
    describe = describe or match_categories
    matched = {}   # (page, cond) -> set(categories)
    parse_fail = {c: 0 for c in CONDITIONS}
    for item in sample:
        for cond in CONDITIONS:
            rec = raw.get(f"{item['page_id']}|{cond}")
            if rec is None:
                continue
            descs, ok = parse_descriptions(rec["raw_text"])
            if not ok:
                parse_fail[cond] += 1
            cats = set()
            for d in descs:
                cats |= describe(d)
            matched[(item["page_id"], cond)] = cats

    summary = {"per_category": {}, "controls": {}, "paired": {}, "parse_fail": parse_fail}

    for cond in CONDITIONS:
        summary["per_category"][cond] = {}
        for cat in DEFECT_TYPES:
            pages = [s["page_id"] for s in sample
                     if cat in s["gt_types"] and (s["page_id"], cond) in matched]
            hits = sum(1 for p in pages if cat in matched[(p, cond)])
            lo, hi = wilson(hits, len(pages))
            summary["per_category"][cond][cat] = {
                "hits": hits, "n": len(pages), "ci": [round(lo, 3), round(hi, 3)]}

        ctrl = [s["page_id"] for s in sample
                if not s["has_defect"] and (s["page_id"], cond) in matched]
        fp = sum(1 for p in ctrl if matched[(p, cond)])
        lo, hi = wilson(fp, len(ctrl))
        summary["controls"][cond] = {"fp": fp, "n": len(ctrl), "ci": [round(lo, 3), round(hi, 3)]}

    # 결함 단위(페이지, 유형) 짝 비교: both vs 단일 모달리티
    for other in ("image_only", "html_only"):
        b = c = 0   # b: both만 탐지, c: other만 탐지
        for s in sample:
            for cat in s["gt_types"]:
                k1, k2 = (s["page_id"], "both"), (s["page_id"], other)
                if k1 not in matched or k2 not in matched:
                    continue
                d1, d2 = cat in matched[k1], cat in matched[k2]
                if d1 and not d2:
                    b += 1
                elif d2 and not d1:
                    c += 1
        summary["paired"][other] = {
            "both_only": b, "other_only": c, "p_mcnemar_exact": mcnemar_exact(b, c)}
    return summary


def print_summary(summary, title="입력 모달리티별 유형 탐지율 (키워드 채점)"):
    print("=" * 78)
    print(title)
    print("=" * 78)
    header = f"{'유형':34s}" + "".join(f"{c:>14s}" for c in CONDITIONS)
    print(header)
    for cat in DEFECT_TYPES:
        row = f"{cat:34s}"
        for cond in CONDITIONS:
            r = summary["per_category"][cond][cat]
            row += f"{r['hits']:>5d}/{r['n']:<3d}({(r['hits']/r['n']*100 if r['n'] else 0):4.0f}%)"
        print(row)
    row = f"{'대조군 오탐(FP)':32s}"
    for cond in CONDITIONS:
        r = summary["controls"][cond]
        row += f"{r['fp']:>5d}/{r['n']:<3d}({(r['fp']/r['n']*100 if r['n'] else 0):4.0f}%)"
    print(row)
    print("-" * 78)
    for other, r in summary["paired"].items():
        print(f"both vs {other:10s}: both만 탐지 {r['both_only']}건, "
              f"{other}만 탐지 {r['other_only']}건, McNemar exact p={r['p_mcnemar_exact']:.3g}")
    print(f"JSON 파싱 실패(원문 전체로 대체 채점): {summary['parse_fail']}")


def score_cmd(_args):
    missing = [p for p in (SAMPLE_PATH, RAW_PATH) if not os.path.exists(p)]
    if missing:
        sys.exit(
            "[오류] 채점할 파일이 없습니다: " + ", ".join(missing) + "\n"
            f"  현재 작업 폴더: {os.getcwd()}\n"
            "  원인 후보: (1) run을 아직 실행하지 않았거나 중간에 오류로 종료됨, "
            "(2) run을 실행한 폴더와 다른 폴더에서 score를 실행함.\n"
            "  해결: run을 먼저 실행하세요 -> python modality_ablation.py run")
    with open(SAMPLE_PATH, "r", encoding="utf-8") as f:
        sample = json.load(f)
    with open(RAW_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    summary = compute_scores(sample, raw)
    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print_summary(summary)
    print(f"\n저장됨: {SUMMARY_PATH}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--per-category", type=int, default=10)
    r.add_argument("--controls", type=int, default=10)
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--resample", action="store_true")
    r.set_defaults(func=run_cmd)
    s = sub.add_parser("score")
    s.set_defaults(func=score_cmd)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()