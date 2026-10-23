"""
cli.py / run_batch_v2.py 실행 산출물의 타당성 점검.

점검 배경 (cli.py / analyzer.py 코드에서 확인된 사실)
  - 점수 = 100 x (1 - 영역별 결함 수 / 30), 사이트 점수는 페이지별 점수의 평균이다. 그래서 종료 코드 1을
    내는 임계값 70 미만은 한 영역에서 페이지당 평균 10건 이상을 보고해야 도달한다 (거의 도달하지 않음).
  - analyzer.py 는 페이지 분석 오류를 결과에서 조용히 빼고, 모든 페이지가 실패하면 결과 없음으로
    cli.py 가 종료 코드 1을 낸다. 일부 페이지만 실패하면 리포트에는 남은 페이지의 이슈만 담긴다.
    => 대상 페이지의 이슈가 0건인 리포트는 '이슈 없음'이 아니라 '분석 실패'일 수 있다.
  - 병렬 스레드가 벡터 저장소를 각자 열어, 검색이 간헐적으로 실패하고 빈 컨텍스트로 진행되었을 수 있다.
    (수정본 analyzer_patched.py 로 다시 실행한 리포트에는 rag_status, page_errors 가 기록된다.)
  - run_batch_v2.py 는 종료 코드가 0이 아니면 재시도하고 같은 경로에 덮어쓴다.

사용법 (대괄호 없이 그대로 입력)
  python audit_pipeline_artifacts.py
  python audit_pipeline_artifacts.py --threshold 70
"""

import os
import sys
import json
import argparse
import statistics
from collections import Counter

TESTBED_DIR = os.environ.get("TESTBED_DIR", "testbed_200")
METADATA_PATH = os.path.join(TESTBED_DIR, "dataset_metadata.json")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results")
MANIFEST_PATH = os.path.join(RESULTS_DIR, "run_manifest.json")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def min_score(report):
    scores = report.get("scores") or {}
    vals = [v for v in scores.values() if isinstance(v, (int, float))]
    return min(vals) if vals else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=int, default=70)
    args = ap.parse_args()

    labels = {}
    if os.path.exists(METADATA_PATH):
        for g in load_json(METADATA_PATH):
            labels[g["page_id"].replace(".html", "")] = bool(g["has_defect"])

    if not labels:
        print(f"[경고] 정답지 메타데이터를 읽지 못했습니다: {METADATA_PATH}\n"
              f"       환경변수 TESTBED_DIR={os.environ.get('TESTBED_DIR')!r} 를 확인하세요 "
              f"(Windows: set TESTBED_DIR=testbed_200). 결함/대조군 구분 집계가 비어 나옵니다.\n")
    print("=" * 72)
    print("[1] 배치 실행 기록 (run_manifest.json)")
    print("=" * 72)
    manifest = load_json(MANIFEST_PATH) if os.path.exists(MANIFEST_PATH) else None
    first_try_lock = {"defect": [0, 0], "control": [0, 0]}   # [잠김, 전체]
    if manifest is None:
        print("run_manifest.json 이 없습니다. 이 절은 건너뜁니다.")
    else:
        pages = manifest.get("pages", [])
        attempts = Counter(p.get("attempt", 1) for p in pages)
        print(f"기록된 페이지 {len(pages)}개, 시도 횟수 분포(최종 시도 번호 기준): {dict(sorted(attempts.items()))}")
        retried = [p["page"] for p in pages if p.get("attempt", 1) > 1 and p.get("success")]
        print(f"재시도 끝에 성공한 페이지: {len(retried)}개 {retried[:20]}{' ...' if len(retried) > 20 else ''}")

        failed = [p for p in pages if not p.get("success")]
        print(f"\n최종 실패 {len(failed)}개:")
        for p in failed:
            pid = f"page_{p['page']:03d}"
            rpath = os.path.join(RESULTS_DIR, f"report_{pid}.json")
            info = f"returncode={p.get('returncode')}, timed_out={p.get('timed_out')}, elapsed={p.get('elapsed_sec')}s"
            if os.path.exists(rpath):
                rep = load_json(rpath)
                ms = min_score(rep)
                reason = ("점수 미달(임계값 70은 영역당 평균 10건 이상에서만 도달)"
                          if ms is not None and ms < args.threshold else
                          "점수는 기준 이상 - 점수 미달이 아닌 다른 원인")
                print(f"  {pid}: 리포트 있음, status={rep.get('status')}, 최저 점수={ms} -> {reason}  ({info})")
            else:
                print(f"  {pid}: 리포트 없음 -> 실제 실행 오류 가능성  ({info})")

        # 첫 시도에서 종료 코드 != 0 이었는지: 최종 시도 번호가 1이고 성공이면 첫 시도 통과
        for p in pages:
            pid = f"page_{p['page']:03d}"
            if pid not in labels:
                continue
            key = "defect" if labels[pid] else "control"
            first_pass = (p.get("attempt", 1) == 1 and p.get("success"))
            first_try_lock[key][1] += 1
            first_try_lock[key][0] += (not first_pass)
        print("\n첫 시도 기준 비통과(종료 코드 != 0) 비율 (점수 미달 외 오류도 포함될 수 있음):")
        for key, (lock, tot) in first_try_lock.items():
            if tot:
                print(f"  {key:8s}: {lock}/{tot} = {lock/tot*100:.1f}%")

    print("\n" + "=" * 72)
    print(f"[2] 리포트의 최저 점수 분포 (기준 {args.threshold}점)")
    print("=" * 72)
    mins = {"defect": [], "control": []}
    n_reports = 0
    sources, with_source, total_issues, with_pagenum = Counter(), 0, 0, 0
    for fn in sorted(os.listdir(RESULTS_DIR)):
        if not (fn.startswith("report_page_") and fn.endswith(".json")):
            continue
        pid = fn[len("report_"):-len(".json")]
        if labels and pid not in labels:
            continue   # 이전 테스트베드(page_01 등)의 리포트는 제외
        rep = load_json(os.path.join(RESULTS_DIR, fn))
        n_reports += 1
        ms = min_score(rep)
        if pid in labels and ms is not None:
            mins["defect" if labels[pid] else "control"].append(ms)
        for issue in rep.get("issues", []) or []:
            if not isinstance(issue, dict):
                continue
            total_issues += 1
            sf = issue.get("source_file")
            if sf not in (None, "", "N/A"):
                with_source += 1
                sources[sf] += 1
            if issue.get("page_number"):
                with_pagenum += 1
    print(f"리포트 {n_reports}개")
    total_counts = Counter()
    for fn in sorted(os.listdir(RESULTS_DIR)):
        if fn.startswith("report_page_") and fn.endswith(".json"):
            pid = fn[len("report_"):-len(".json")]
            if labels and pid not in labels:
                continue
            total_counts[load_json(os.path.join(RESULTS_DIR, fn)).get("total_count")] += 1
    print(f"리포트의 total_count(페이지별 점검 항목 수의 합) 분포: {dict(sorted(total_counts.items(), key=lambda kv: str(kv[0])))}")
    print("  (프롬프트 예시값 30이 아니라 모델이 임의로 보고한 값이 섞여 있으면 점수가 페이지마다 비교 불가능해진다)")
    for key, vals in mins.items():
        if vals:
            below = sum(v < args.threshold for v in vals)
            print(f"  {key:8s}: n={len(vals)}, 평균 {statistics.mean(vals):.1f}, 중앙값 {statistics.median(vals):.1f}, "
                  f"최소 {min(vals)}, 기준 미달 {below}개")

    print("\n" + "=" * 72)
    print("[3] RAG 근거 기록 (issue의 source_file / page_number)")
    print("=" * 72)
    print(f"전체 issue {total_issues}건 중 source_file이 N/A가 아닌 것: {with_source}건, page_number가 있는 것: {with_pagenum}건")
    for sf, c in sources.most_common(5):
        print(f"  {sf}: {c}건")
    if total_issues and with_source == 0:
        print("-> source_file이 전부 N/A입니다. RAG 컨텍스트가 프롬프트에 들어갔는지, 들어갔다면 출처가 "
              "기록되지 않는 것인지 analyzer.py에서 확인해야 합니다.")

    # [4] 대상 페이지 이슈 0건 리포트, 수정본이 기록한 필드
    print("\n" + "=" * 72)
    print("[4] 대상 페이지 이슈가 0건인 리포트 / 수정본 기록 필드")
    print("=" * 72)
    base = os.path.basename(os.path.normpath(TESTBED_DIR))
    zero_on_target, rag_fail, rag_total, page_err_total, has_new_fields = [], 0, 0, 0, 0
    for fn in sorted(os.listdir(RESULTS_DIR)):
        if not (fn.startswith("report_page_") and fn.endswith(".json")):
            continue
        pid = fn[len("report_"):-len(".json")]
        if labels and pid not in labels:
            continue
        rep = load_json(os.path.join(RESULTS_DIR, fn))
        marker = f"{base}/{pid}.html"
        n_on = sum(1 for i in rep.get("issues", []) or []
                   if isinstance(i, dict) and marker in str(i.get("description", "")))
        if n_on == 0:
            zero_on_target.append(pid)
        if "rag_status" in rep or "page_errors" in rep:
            has_new_fields += 1
            for r in rep.get("rag_status", []):
                rag_total += 1
                rag_fail += (not r.get("ok") and r.get("enabled", True))
            page_err_total += len(rep.get("page_errors", []))
    print(f"대상 페이지 이슈 0건: {len(zero_on_target)}개 {zero_on_target}")
    print("  -> 분석 실패로 조용히 제외된 페이지일 수 있으므로 '이슈 없음'으로 해석하지 말고 평가에서 제외하는 것을 검토하세요.")
    if has_new_fields:
        print(f"수정본 필드가 있는 리포트 {has_new_fields}개: 페이지 분석 {rag_total}건 중 검색 실패 {rag_fail}건, "
              f"분석 오류로 제외된 페이지 {page_err_total}건")
    else:
        print("수정본 필드(rag_status, page_errors)가 있는 리포트가 없습니다. (원본 analyzer.py 로 실행한 결과)")

    # [5] CLI 종단 지연시간: manifest 의 elapsed_sec (한 번 실행당 사이트 전체 분석, 페이지 병렬)
    print("\n" + "=" * 72)
    print("[5] CLI 종단 지연시간 (첫 시도에 성공한 실행의 elapsed_sec)")
    print("=" * 72)
    if manifest is not None:
        vals = sorted(p["elapsed_sec"] for p in manifest.get("pages", [])
                      if p.get("success") and p.get("attempt", 1) == 1 and p.get("elapsed_sec") is not None)
        if vals:
            def pct(q):
                return vals[min(len(vals) - 1, int(round(q * (len(vals) - 1))))]
            print(f"n={len(vals)}, 평균 {statistics.mean(vals):.1f}s, 표준편차 {statistics.pstdev(vals):.1f}s, "
                  f"중앙값 {statistics.median(vals):.1f}s, P95 {pct(0.95):.1f}s, 최대 {vals[-1]:.1f}s")
            print("  (페이지 로드 후 3초 대기, 최대 5개 페이지 병렬 분석, 이미지 업로드 포함. 별도 측정 스크립트의 값과 다르다.)")
        else:
            print("해당하는 기록이 없습니다.")

    print("\n해석 가이드")
    print("- [1]의 실패 페이지에 리포트가 있고 최저 점수가 기준 미만이면, 제외 사유를 '점수 미달'로 정정하고 "
          "해당 페이지를 평가에 포함해야 합니다 (결함이 많은 페이지만 빠졌을 수 있음).")
    print("- [1]의 재시도 성공 페이지가 많다면, 그 페이지의 리포트는 마지막 시도 결과이므로 선택 편향을 명시하세요.")


if __name__ == "__main__":
    main()