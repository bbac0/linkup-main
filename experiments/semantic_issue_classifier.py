"""
키워드 매칭 대신 LLM이 각 issue를 5개 결함 유형(또는 None)으로 분류하여
Table 1/2의 수치를 다시 산정한다. 키워드 채점과의 일치도도 함께 보여 준다.

왜 필요한가
  키워드 사전은 (1) 표현이 다르면 실제 보고를 놓치고(page_005, page_140),
  (2) 'WCAG', 'viewport' 같은 포괄 구문이 무관한 언급을 탐지로 오인한다.
  의미 기반 분류는 두 오류를 모두 줄이지만, 채점자 자체의 편향이 새로 생긴다.

사용법
  python semantic_issue_classifier.py --dry-run            # API 없이 흐름만 점검(키워드로 흉내)
  python semantic_issue_classifier.py --limit 20           # 20개 페이지만 시험
  python semantic_issue_classifier.py                      # 전체 (캐시 사용, 중단 후 재개 가능)
  python semantic_issue_classifier.py --provider anthropic # 다른 모델 계열로 채점

환경변수
  GEMINI_API_KEY   (--provider gemini, 기본)
  ANTHROPIC_API_KEY, JUDGE_MODEL   (--provider anthropic; 모델명은 직접 지정)
  JUDGE_MODEL      (gemini 기본값 gemini-2.5-flash)
  TESTBED_DIR      (기본 testbed_200)

중요한 주의
  - 아래 JUDGE_PROMPT의 유형 정의는 결과를 보기 전에 고정한다. 결과를 보고 정의를
    고치면 채점 기준을 결과에 맞춘 것이 되므로, 고쳤다면 논문에 그 사실을 적어야 한다.
  - 평가 대상이 Gemini이므로 Gemini를 채점자로 쓰면 자기 선호 편향 가능성이 있다.
    가능하면 --provider anthropic으로 채점하거나, kappa_validation.py로 사람 검증을 추가한다.
  - 정의는 키워드 사전과 의도적으로 다르다: 'viewport meta 태그 누락'은 레이아웃 붕괴가
    아니라 마크업 권고로 보아 None으로 분류한다.
"""

import os
import re
import sys
import json
import time
import math
import random
import hashlib
import argparse

TESTBED_DIR = "testbed_200"  # os.environ.get 대신 직접 지정
METADATA_PATH = os.path.join(TESTBED_DIR, "dataset_metadata.json")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results")
MANIFEST_PATH = os.path.join(RESULTS_DIR, "run_manifest.json")
CACHE_PATH = os.path.join(RESULTS_DIR, "semantic_cache.json")
OUT_PATH = os.path.join(RESULTS_DIR, "semantic_scoring.json")

PROMPT_VERSION = "v1"
DEFECT_TYPES = [
    "Poor Contrast Ratio",
    "Mobile Layout Breakage",
    "Button Area Clipping",
    "Standard Terminology Violation",
    "Duplicate ID Markup Error",
]
LABELS = DEFECT_TYPES + ["None"]

JUDGE_PROMPT = """다음은 웹페이지 자동 검수 시스템이 보고한 결함 한 건의 설명문입니다. 아래 다섯 가지 결함 유형 중, 이 설명문이 주된 지적 내용으로 삼는 유형을 하나만 고르세요. 어느 유형에도 해당하지 않으면 None을 고르세요.

[유형 정의]
- Poor Contrast Ratio: 텍스트 색과 배경색의 명도 대비가 부족하여 글자를 읽기 어렵다는 지적.
- Mobile Layout Breakage: 고정 너비, 강제 가로 스크롤 등으로 화면 폭에 따라 레이아웃이 깨지거나 요소가 영역 밖으로 넘친다는 지적. viewport meta 태그가 없다는 지적 자체는 여기에 포함하지 않는다.
- Button Area Clipping: 버튼이 CSS(높이, 글꼴 크기, overflow 등) 때문에 잘리거나, 거의 보이지 않거나, 클릭할 수 없다는 지적.
- Standard Terminology Violation: 링크, 버튼, 제목 같은 UI 레이블의 용어가 비표준이거나 모호하거나 부적절하므로 표준적이고 구체적인 용어로 바꿔야 한다는 지적.
- Duplicate ID Markup Error: 같은 id 속성 값이 한 문서에서 둘 이상의 요소에 쓰였다는 지적.
- None: 위 다섯 가지에 해당하지 않는 모든 지적 (예: autocomplete 속성 누락, 레이블과 입력 필드의 연결, 제목 태그 계층, 정보 부족, 빈 페이지, 스타일 일관성, 일반 접근성 권고).

[설명문]
<<DESC>>

JSON으로만 답하세요: {"label": "유형 이름 또는 None"}"""

# 키워드 채점과의 비교용 (evaluate_performance_v5.py와 동일)
CATEGORY_PHRASES = {
    "Poor Contrast Ratio": ["명도 대비", "대비 비율", "contrast ratio", "wcag", "가독성"],
    "Mobile Layout Breakage": ["레이아웃이 깨", "레이아웃 깨짐", "가로 스크롤", "viewport",
                               "모바일 화면", "반응형", "overflow"],
    "Button Area Clipping": ["버튼이 잘", "버튼 잘림", "버튼 영역", "clipping", "겹침", "overlap",
                             "클릭 영역", "클릭할 수 없", "클릭할수 없", "상호작용이 불가능",
                             "시각적으로 거의 보이지", "font-size: 0px", "height: 2px"],
    "Standard Terminology Violation": ["표준 용어", "용어 위반", "terminology", "용어 불일치",
                                       "용어가 표준", "접속하기"],
    "Duplicate ID Markup Error": ["id가 중복", "중복된 id", "duplicate id", "동일한 id",
                                  "id 속성이 중복", "wai-aria", "두 번 사용되어", "중복 사용"],
}


def keyword_label(desc):
    low = desc.lower()
    for cat, phrases in CATEGORY_PHRASES.items():
        if any(p.lower() in low for p in phrases):
            return cat
    return "None"


def keyword_labels_all(desc):
    low = desc.lower()
    return {c for c, ps in CATEGORY_PHRASES.items() if any(p.lower() in low for p in ps)}


def wilson(s, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = s / n
    d = 1 + z ** 2 / n
    c = p + z ** 2 / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2))
    return (max(0.0, (c - m) / d), min(1.0, (c + m) / d))


# ---------------------------------------------------------------------------
# 채점자 호출
# ---------------------------------------------------------------------------

def with_backoff(fn, retries=6):
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            msg = str(e).lower()
            if not any(k in msg for k in ("429", "rate", "quota", "503", "unavailable", "overloaded")) \
                    or attempt == retries:
                raise
            wait = min(60, 2 ** attempt) + random.uniform(0, 1)
            print(f"  [재시도 {attempt}/{retries}] {wait:.1f}초 대기")
            time.sleep(wait)


def make_judge(provider, dry_run):
    if dry_run:
        return lambda desc: json.dumps({"label": keyword_label(desc)})

    if provider == "gemini":
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            sys.exit("GEMINI_API_KEY가 필요합니다.")
        import google.generativeai as genai
        genai.configure(api_key=key)
        model = genai.GenerativeModel(
            os.environ.get("JUDGE_MODEL", "gemini-2.5-flash"),
            generation_config={"temperature": 0})
        return lambda desc: with_backoff(
            lambda: model.generate_content(JUDGE_PROMPT.replace("<<DESC>>", desc)).text)

    if provider == "anthropic":
        model_name = os.environ.get("JUDGE_MODEL")
        if not model_name or not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("ANTHROPIC_API_KEY와 JUDGE_MODEL(모델명)이 필요합니다.")
        import anthropic
        client = anthropic.Anthropic()

        def call(desc):
            msg = client.messages.create(
                model=model_name, max_tokens=100, temperature=0,
                messages=[{"role": "user", "content": JUDGE_PROMPT.replace("<<DESC>>", desc)}])
            return msg.content[0].text
        return lambda desc: with_backoff(lambda: call(desc))

    sys.exit(f"알 수 없는 provider: {provider}")


def parse_label(text):
    m = re.search(r'"label"\s*:\s*"([^"]+)"', text or "")
    cand = m.group(1).strip() if m else ""
    if cand in LABELS:
        return cand, True
    for lab in LABELS:                       # JSON이 아니어도 유형 이름이 하나만 나오면 인정
        if lab != "None" and lab in (text or ""):
            return lab, True
    return "None", False


# ---------------------------------------------------------------------------
# 데이터 로딩 / 채점
# ---------------------------------------------------------------------------

def load_failed():
    if not os.path.exists(MANIFEST_PATH):
        return set()
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            return {f"page_{i:03d}" for i in json.load(f).get("failed_pages", [])}
    except Exception:
        return set()


def on_target_descriptions(report, page_id):
    marker = f"{os.path.basename(os.path.normpath(TESTBED_DIR))}/{page_id}.html"
    out = []
    for issue in report.get("issues", []) or []:
        if isinstance(issue, dict):
            d = issue.get("description", "")
            if isinstance(d, str) and marker in d:
                out.append(d)
    return out


def confusion(rows, pred_key):
    tp = fp = fn = tn = 0
    for r in rows:
        pos = len(r[pred_key]) > 0
        if r["has_defect"] and pos:
            tp += 1
        elif not r["has_defect"] and pos:
            fp += 1
        elif r["has_defect"]:
            fn += 1
        else:
            tn += 1
    return tp, fp, fn, tn


def evaluate(rows):
    out = {}
    for name, key in (("semantic", "pred_sem"), ("keyword", "pred_kw")):
        tp, fp, fn, tn = confusion(rows, key)
        cats = {}
        for cat in DEFECT_TYPES:
            gt_pages = [r for r in rows if cat in r["gt_types"]]
            hits = sum(1 for r in gt_pages if cat in r[key])
            cats[cat] = (hits, len(gt_pages))
        out[name] = {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "cats": cats}
    # 일치도
    agree = {}
    for cat in DEFECT_TYPES:
        both = sem_only = kw_only = neither = 0
        for r in rows:
            if cat not in r["gt_types"]:
                continue
            s, k = cat in r["pred_sem"], cat in r["pred_kw"]
            if s and k:
                both += 1
            elif s:
                sem_only += 1
            elif k:
                kw_only += 1
            else:
                neither += 1
        agree[cat] = {"both": both, "semantic_only": sem_only, "keyword_only": kw_only, "neither": neither}
    out["agreement"] = agree
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="gemini", choices=["gemini", "anthropic"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.3)
    args = ap.parse_args()

    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)
    failed = load_failed()
    judge = make_judge(args.provider, args.dry_run)

    cache = {}
    cache_path = CACHE_PATH if not args.dry_run else CACHE_PATH + ".dryrun"
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)

    rows, parse_fail, calls = [], 0, 0
    for g in meta:
        page_id = g["page_id"].replace(".html", "")
        if page_id in failed:
            continue
        rp = os.path.join(RESULTS_DIR, f"report_{page_id}.json")
        if not os.path.exists(rp):
            continue
        if args.limit and len(rows) >= args.limit:
            break
        with open(rp, "r", encoding="utf-8") as f:
            report = json.load(f)

        detail, sem, kw = [], set(), set()
        for desc in on_target_descriptions(report, page_id):
            ck = hashlib.sha1(f"{PROMPT_VERSION}|{args.provider}|{desc}".encode("utf-8")).hexdigest()
            if ck not in cache:
                label, ok = parse_label(judge(desc))
                cache[ck] = {"label": label, "ok": ok}
                calls += 1
                time.sleep(0 if args.dry_run else args.sleep)
                if calls % 25 == 0:
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(cache, f, ensure_ascii=False)
            label, ok = cache[ck]["label"], cache[ck]["ok"]
            parse_fail += (not ok)
            if label != "None":
                sem.add(label)
            kw |= keyword_labels_all(desc)
            detail.append({"desc": desc[:140], "semantic": label})

        rows.append({
            "page_id": page_id, "has_defect": g["has_defect"],
            "gt_types": sorted({d["type"] for d in g["defects"]}),
            "pred_sem": sorted(sem), "pred_kw": sorted(kw), "issues": detail})

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)

    res = evaluate(rows)
    with open(OUT_PATH if not args.dry_run else OUT_PATH + ".dryrun", "w", encoding="utf-8") as f:
        json.dump({"summary": res, "pages": rows, "prompt_version": PROMPT_VERSION,
                   "provider": args.provider, "dry_run": args.dry_run}, f, ensure_ascii=False, indent=2)

    print("=" * 74)
    print(f"의미 기반 채점 결과 (평가 페이지 {len(rows)}개, 새 호출 {calls}건, 응답 파싱 실패 {parse_fail}건"
          f"{', DRY-RUN(키워드로 흉내)' if args.dry_run else ''})")
    print("=" * 74)
    for name in ("semantic", "keyword"):
        r = res[name]
        tp, fp, fn, tn = r["tp"], r["fp"], r["fn"], r["tn"]
        rec = tp / (tp + fn) if tp + fn else 0
        fpr = fp / (fp + tn) if fp + tn else 0
        print(f"[{name:8s}] TP/FP/FN/TN = {tp}/{fp}/{fn}/{tn}   page Recall {rec:.4f}   FPR {fpr:.4f}")
    print("-" * 74)
    print(f"{'유형':34s}{'semantic':>14s}{'keyword':>14s}   일치(둘다/의미만/키워드만/둘다X)")
    for cat in DEFECT_TYPES:
        s, k = res["semantic"]["cats"][cat], res["keyword"]["cats"][cat]
        a = res["agreement"][cat]
        print(f"{cat:34s}{s[0]:>7d}/{s[1]:<5d}{k[0]:>7d}/{k[1]:<5d}   "
              f"{a['both']}/{a['semantic_only']}/{a['keyword_only']}/{a['neither']}")
    tot_s = sum(v[0] for v in res["semantic"]["cats"].values())
    tot_k = sum(v[0] for v in res["keyword"]["cats"].values())
    tot_n = sum(v[1] for v in res["semantic"]["cats"].values())
    lo, hi = wilson(tot_s, tot_n)
    print(f"{'결함 단위 Recall(5개 유형)':30s}{tot_s:>7d}/{tot_n:<5d}{tot_k:>7d}/{tot_n:<5d}"
          f"   semantic 95% CI ({lo:.3f}, {hi:.3f})")
    print(f"\n상세: {OUT_PATH}  (페이지별 issue와 분류 라벨 포함, 감사용)")


if __name__ == "__main__":
    main()