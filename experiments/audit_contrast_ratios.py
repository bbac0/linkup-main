"""
모델이 리포트에 적은 명도 대비 수치가 WCAG 공식 값과 얼마나 다른지 감사한다.

배경
  같은 색 조합(#e5e5e5 대 #ffffff)에 대해 리포트의 수치가 페이지마다
  1.1 ~ 1.27로 달랐다. WCAG 공식으로 계산하면 약 1.26이다. 이 스크립트는
  Poor Contrast Ratio가 정답인 모든 페이지에서 (1) HTML의 색상 값으로
  공식 대비비를 계산하고 (2) 리포트 설명문에서 모델이 보고한 대비비를 뽑아
  오차를 요약한다. API 호출은 하지 않는다 (기존 리포트와 HTML만 사용).

사용법
  python audit_contrast_ratios.py

부산물
  wcag_contrast() 함수는 그대로 cli.py에 가져가 DOM에서 뽑은 색상으로 대비비를
  미리 계산하고 프롬프트에 넣는 용도로 쓸 수 있다 (하이브리드 설계).
"""

import os
import re
import csv
import json
import statistics

METADATA_PATH = os.path.join("testbed_200", "dataset_metadata.json")
METADATA_PATH = os.path.join(TESTBED_DIR, "dataset_metadata.json")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results")
OUT_CSV = os.path.join(RESULTS_DIR, "contrast_audit.csv")

# HTML에서 색상을 찾지 못할 때의 대체값 (테스트베드의 주입 색상). 대체 사용 시 표시한다.
FALLBACK_FG = os.environ.get("FALLBACK_FG", "#e5e5e5")
FALLBACK_BG = os.environ.get("FALLBACK_BG", "#ffffff")

STANDARD_THRESHOLDS = {3.0, 4.5, 7.0}   # 기준값으로 언급된 수치는 '보고 수치'에서 제외
RATIO_RE = re.compile(r"(\d+(?:\.\d+)?)\s*:\s*1")


# ---------------------------------------------------------------------------
# WCAG 대비비 (상대 휘도 공식)
# ---------------------------------------------------------------------------

def _lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def parse_hex(h):
    h = h.strip().lstrip("#")
    if len(h) in (3, 4):
        h = "".join(ch * 2 for ch in h)
    h = h[:6]
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def luminance(rgb):
    r, g, b = rgb
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def wcag_contrast(fg_hex, bg_hex):
    l1, l2 = luminance(parse_hex(fg_hex)), luminance(parse_hex(bg_hex))
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------------------
# HTML / 리포트 파싱
# ---------------------------------------------------------------------------

def extract_colors(html):
    """<style> 블록에서 첫 전경색/배경색을 추출. (fg, bg, 출처) 반환."""
    blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.S | re.I)
    css = "\n".join(blocks) if blocks else html
    fg = re.search(r"(?<![\w-])color\s*:\s*(#[0-9a-fA-F]{3,8})", css)
    bg = re.search(r"background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,8})", css)
    if fg and bg:
        return fg.group(1), bg.group(1), "html"
    return FALLBACK_FG, FALLBACK_BG, "fallback"


def reported_ratios(description):
    vals = [float(x) for x in RATIO_RE.findall(description)]
    return [v for v in vals if v not in STANDARD_THRESHOLDS and v < 3.0]


def on_target_descriptions(report, page_id):
    marker = f"{os.path.basename(os.path.normpath(TESTBED_DIR))}/{page_id}.html"
    out = []
    for issue in report.get("issues", []):
        if isinstance(issue, dict):
            d = issue.get("description", "")
            if isinstance(d, str) and marker in d:
                out.append(d)
    return out


def main():
    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)

    rows = []
    for g in meta:
        if not any(d["type"] == "Poor Contrast Ratio" for d in g["defects"]):
            continue
        page_id = g["page_id"].replace(".html", "")
        report_path = os.path.join(RESULTS_DIR, f"report_{page_id}.json")
        html_path = os.path.join(TESTBED_DIR, g["page_id"])
        if not (os.path.exists(report_path) and os.path.exists(html_path)):
            continue

        with open(html_path, "r", encoding="utf-8") as f:
            fg, bg, src = extract_colors(f.read())
        truth = wcag_contrast(fg, bg)

        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        ratios = []
        for d in on_target_descriptions(report, page_id):
            ratios += reported_ratios(d)

        rep = ratios[0] if ratios else None
        rows.append({
            "page_id": page_id, "fg": fg, "bg": bg, "color_source": src,
            "true_ratio": round(truth, 3),
            "reported_ratio": rep,
            "abs_error": round(abs(rep - truth), 3) if rep is not None else None,
        })

    os.makedirs(RESULTS_DIR, exist_ok=True)
    if rows:
        with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    n = len(rows)
    with_num = [r for r in rows if r["reported_ratio"] is not None]
    print("=" * 64)
    print("명도 대비 수치 감사 (Poor Contrast Ratio 정답 페이지)")
    print("=" * 64)
    print(f"대상 페이지: {n}개 / 리포트에 수치가 있는 페이지: {len(with_num)}개")
    if n:
        print(f"색상 출처: HTML에서 추출 {sum(r['color_source']=='html' for r in rows)}개, "
              f"대체값 사용 {sum(r['color_source']=='fallback' for r in rows)}개")
        print(f"공식 대비비(고유값): {sorted({r['true_ratio'] for r in rows})}")
    if with_num:
        vals = [r["reported_ratio"] for r in with_num]
        errs = [r["abs_error"] for r in with_num]
        print(f"모델 보고 수치: 최소 {min(vals)}, 최대 {max(vals)}, 평균 {statistics.mean(vals):.3f}, "
              f"표준편차 {statistics.pstdev(vals):.3f}, 고유값 {len(set(vals))}종")
        print(f"평균 절대오차: {statistics.mean(errs):.3f}")
        print(f"오차 0.05 이내: {sum(e <= 0.05 for e in errs)}/{len(errs)}, "
              f"0.10 이내: {sum(e <= 0.10 for e in errs)}/{len(errs)}")
    print(f"\n페이지별 결과: {OUT_CSV}")
    print("해석: 같은 색 조합인데 보고 수치의 고유값이 여러 종이면, 모델이 대비비를 "
          "계산하지 않고 추정한다는 근거가 된다.")


if __name__ == "__main__":
    main()