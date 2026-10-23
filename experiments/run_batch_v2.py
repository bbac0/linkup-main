"""
200페이지 순차 실행 러너 (v2)

v1 대비 변경 사항:
  1. 페이지 간 대기시간을 0.5초 -> 2.5초로 늘려, 이전 프로세스가 브라우저
     프로세스 및 chroma_db의 SQLite 파일 핸들을 완전히 반환할 시간을 확보.
  2. 각 subprocess에 timeout을 걸어, 행(hang) 상태로 무한정 멈추는 것을
     방지 (타임아웃 시 강제 종료 후 실패로 기록하고 다음 페이지로 진행).
  3. 실패(non-zero return code 또는 timeout)한 페이지는 즉시 포기하지 않고
     짧은 대기 후 최대 MAX_RETRIES회까지 재시도.
  4. 전체 실행 결과(성공/실패/재시도 횟수)를 results/run_manifest.json에
     기록하여, 미팅 전 "실제로 몇 개가 성공했는지"를 바로 확인 가능하게 함.
  5. 재시도까지 다 실패한 페이지 번호를 따로 모아 출력하여, 나중에 그
     페이지들만 골라 재실행할 수 있게 함.
"""

import subprocess
import os
import sys
import time
import json
import datetime

# 결과 폴더를 환경변수로 분리한다 (예: RESULTS_DIR=results_rag_on).
RESULTS_DIR = os.environ.get("RESULTS_DIR", "results")
# 평가에서는 점수 게이트가 필요 없다. 임계값이 70이면 점수가 낮은 페이지가 종료 코드 1이 되어
# '실패'로 재시도되고 리포트가 덮어써진다. 0이면 분석 오류일 때만 종료 코드가 1이 된다.
GATE_THRESHOLD = os.environ.get("GATE_THRESHOLD", "0")
os.makedirs(RESULTS_DIR, exist_ok=True)

python_executable = sys.executable

TOTAL_COUNT = 200
SLEEP_BETWEEN_RUNS_SEC = 2.5
PER_PAGE_TIMEOUT_SEC = 120  # 한 페이지 분석이 이보다 오래 걸리면 강제 종료
MAX_RETRIES = 2
RETRY_BACKOFF_SEC = 5

manifest = {
    "started_at": datetime.datetime.now().isoformat(),
    "total_count": TOTAL_COUNT,
    "pages": [],
}


def run_one_page(i: int, attempt: int) -> dict:
    url = f"http://localhost:8080/testbed_200/page_{i:03d}.html"
    output_json = os.path.join(RESULTS_DIR, f"report_page_{i:03d}.json")

    print(f"\n[실험 {i}/{TOTAL_COUNT}] (시도 {attempt}/{MAX_RETRIES + 1}) "
          f"분석 진행 중: {url}")

    start = time.time()
    try:
        proc = subprocess.run(
            [
                python_executable, "cli.py",
                "--url", url,
                "--threshold", GATE_THRESHOLD,
                "--output", output_json,
            ],
            timeout=PER_PAGE_TIMEOUT_SEC,
        )
        elapsed = time.time() - start
        success = (proc.returncode == 0) and os.path.exists(output_json)
        return {
            "page": i,
            "attempt": attempt,
            "success": success,
            "returncode": proc.returncode,
            "elapsed_sec": round(elapsed, 2),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"  [타임아웃] {PER_PAGE_TIMEOUT_SEC}초 초과, 프로세스 강제 종료됨.")
        return {
            "page": i,
            "attempt": attempt,
            "success": False,
            "returncode": None,
            "elapsed_sec": round(elapsed, 2),
            "timed_out": True,
        }


def main():
    failed_pages_final = []

    for i in range(1, TOTAL_COUNT + 1):
        attempt = 1
        result = run_one_page(i, attempt)

        while not result["success"] and attempt <= MAX_RETRIES:
            print(f"  [재시도 대기] {RETRY_BACKOFF_SEC}초 후 재시도합니다...")
            time.sleep(RETRY_BACKOFF_SEC)
            attempt += 1
            result = run_one_page(i, attempt)

        manifest["pages"].append(result)

        if not result["success"]:
            failed_pages_final.append(i)
            print(f"  [최종 실패] page_{i:03d} — {MAX_RETRIES + 1}회 시도 후에도 실패.")
        else:
            print(f"  [성공] page_{i:03d} 완료 ({result['elapsed_sec']}s)")

        # 다음 페이지 실행 전 대기: 이전 프로세스의 브라우저/DB 파일 핸들이
        # 완전히 반환될 시간을 확보 (0.5초 -> 2.5초로 상향)
        time.sleep(SLEEP_BETWEEN_RUNS_SEC)

    success_count = sum(1 for p in manifest["pages"] if p["success"])
    manifest["finished_at"] = datetime.datetime.now().isoformat()
    manifest["success_count"] = success_count
    manifest["failed_pages"] = failed_pages_final

    with open(os.path.join(RESULTS_DIR, "run_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print(f"전체 실행 완료: {TOTAL_COUNT}개 중 성공 {success_count}개, "
          f"실패 {len(failed_pages_final)}개")
    if failed_pages_final:
        print(f"최종 실패 페이지 번호: {failed_pages_final}")
        print(f"이 페이지들은 {RESULTS_DIR}/report_page_XXX.json이 생성되지 "
              "않았을 가능성이 높습니다. 분석 시 N에서 제외하거나 따로 "
              "재실행하세요.")
    print(f"상세 기록: {os.path.join(RESULTS_DIR, 'run_manifest.json')}")
    print("=" * 60)


if __name__ == "__main__":
    main()