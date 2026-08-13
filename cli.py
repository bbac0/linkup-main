import sys
import asyncio
import argparse
import json
import os
from services.analyzer import run_analysis_flow

async def main():
    # 커맨드라인 인자 설정
    parser = argparse.ArgumentParser(description="웹 UI/UX 및 코드 품질 자동 검수 CLI (CI/CD용)")
    parser.add_argument("--url", required=True, help="분석할 웹사이트의 메인 URL")
    parser.add_argument("--threshold", type=int, default=70, help="빌드 통과를 위한 최소 점수 커트라인 (기본값: 70)")
    parser.add_argument("--output", default="audit_report.json", help="결과 리포트를 저장할 JSON 파일 경로")
    parser.add_argument("--pdf", nargs="*", default=[], help="추가 분석용 가이드라인 PDF 파일 경로 리스트")
    
    args = parser.parse_args()

    print("="*60)
    print(f"[CI/CD Automated Audit] 파이프라인 가동")
    print(f"Target URL: {args.url}")
    print(f"Threshold (최소 통과 점수): {args.threshold}점")
    print("="*60 + "\n")

    final_result = None

    # 분석 엔진 구동 및 실시간 상태 모니터링
    try:
        async for step in run_analysis_flow(target_url=args.url, is_any_checked=args.pdf, upload_file=[]):
            
            # 진행 상태 텍스트 출력 (CI/CD 로그용)
            if step.get("text"):
                progress = step.get("progress", 0)
                if progress >= 0:
                    print(f"⏳ [진행률 {progress}%] {step['text']}")
                else:
                    print(f"⚠️ {step['text']}")
            
            # 최종 결과 데이터 포착
            if "result" in step:
                final_result = step["result"]

    except Exception as e:
        print(f"\n[FATAL ERROR] 분석 프로세스 중 치명적 오류가 발생했습니다: {e}")
        sys.exit(1)

    # 결과 검증 및 빌드 락(Lock) 처리
    if not final_result or final_result.get("status") == "error":
        error_msg = final_result.get("message", "원인을 알 수 없는 오류") if final_result else "결과 데이터를 받지 못했습니다."
        print(f"\n[BUILD FAIL] 분석 실패: {error_msg}")
        sys.exit(1)

    # CI/CD 아티팩트용 JSON 파일 저장
    try:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(final_result, f, ensure_ascii=False, indent=2)
        print(f"\n📄 전체 분석 결과가 리포트 파일로 저장되었습니다: {os.path.abspath(args.output)}")
    except Exception as e:
        print(f"\nJSON 리포트 파일 저장 중 오류 발생: {e}")

    # 점수 기반 통과 여부 판별
    scores = final_result.get("scores", {})
    if not scores:
        print("\n[BUILD FAIL] 산출된 점수 데이터가 없습니다.")
        sys.exit(1)

    print("\n카테고리별 검수 점수:")
    for category, score in scores.items():
        print(f"   - {category}: {score}점")

    min_score = min(scores.values())
    
    print("\n" + "="*60)
    if min_score < args.threshold:
        print(f"[ACTION REQUIRED] 배포 차단 (Build Locked)")
        print(f"이유: 최저 점수({min_score}점)가 설정된 기준치({args.threshold}점)에 미달합니다.")
        print("="*60)
        sys.exit(1)  # Exit Code 1: 실패
    else:
        print(f"[SUCCESS] 모든 품질 검사를 통과했습니다. 배포를 계속 진행합니다.")
        print("="*60)
        sys.exit(0)  # Exit Code 0: 성공

if __name__ == "__main__":
    # Windows 환경에서 Playwright 비동기 오류 방지를 위한 정책 설정
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    asyncio.run(main())