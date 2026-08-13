import google.generativeai as genai 
import streamlit as st
import os
import time
import json
import re
import asyncio

from datetime import datetime
from PyPDF2 import PdfReader 
from playwright.async_api import async_playwright
from urllib.parse import urlparse 
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma


def calculate_score(total, fault_count):
    if total == 0: return 0
    raw_score = (1 - fault_count / total) * 100
    return int(max(0, round(raw_score)))

def analyze_with_gemini(html_content: str, screenshot_path: str, pdf_paths: list) -> dict:

    if not html_content or len(html_content) < 100:
        print(f"경고: HTML 내용이 너무 짧거나 비어있습니다. (길이: {len(html_content)})")
    if not os.path.exists(screenshot_path):
        print(f"경고: 스크린샷 파일을 찾을 수 없습니다: {screenshot_path}")
    
    start_time = time.time()

    # API 세팅
    api_key = os.getenv("GEMINI_API_KEY")
    genai.configure(api_key=api_key)
    os.environ["GOOGLE_API_KEY"] = api_key
    
    # 베이스 DB 검색 로직
    retrieved_context = ""
    try:
        embeddings = GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-001")
        vector_store = Chroma(persist_directory="./chroma_db", embedding_function=embeddings)
        docs = vector_store.similarity_search("웹 UI/UX 표준 가이드라인 공통 규정", k=4)
        context_list = []
        for doc in docs:
            page_num = doc.metadata.get('page', 0) + 1 
            source_file = os.path.basename(doc.metadata.get('source', '정부가이드라인.pdf'))
            context_list.append(f"[{source_file} - {page_num}페이지]\n{doc.page_content}")
        retrieved_context = "\n\n".join(context_list)
    except Exception as e:
        print(f"베이스 DB 검색 실패: {e}")

    # 커스텀 PDF 추출 로직
    user_custom_context = ""
    if pdf_paths:
        user_files = [p for p in pdf_paths if "temp" in p or "uploads" in p]
        if user_files:
            for path in user_files:
                try:
                    reader = PdfReader(path)
                    pdf_name = os.path.basename(path)
                    for i, page in enumerate(reader.pages):
                        user_custom_context += f"[{pdf_name} - {i+1}페이지]\n{page.extract_text()}\n"
                except Exception as e:
                    print(f"사용자 PDF 읽기 실패({path}): {e}")
        
    image_file = genai.upload_file(screenshot_path)
    
    today = datetime.now().strftime("%Y년 %m월 %d일")
    
    prompt = f"""
    너는 전문적인 UI/UX 및 코드 품질 검수관이야.
    첨부된 '모든' 가이드라인 문서(PDF)를 절대적인 기준으로 삼아, 
    제공된 웹사이트 스크린샷과 HTML 소스 코드를 분석해줘.

    [1. 정부 표준 가이드라인 (기본 지침)]
    {retrieved_context}

    [2. 사용자 지정 추가 가이드라인 (우선 적용)]
    {user_custom_context if user_custom_context else "추가 지침 없음"}

    현재 시점은 **{today}**이고, 모든 최신성 분석은 이 연도를 기준으로 해줘.
    가이드라인보다 더 좋은 방향이 있다면 추가적으로 남겨줘.
    
    ### 카테고리 분류 기준:
    1. **디자인**: 시각적 요소, 레이아웃, 명도 대비, '보이스 앤 톤', 버튼의 텍스트 레이블 등 사용자 경험과 관련된 사항
    2. **운영**: 콘텐츠의 최신성, 오타, 링크 연결 오류, 가이드라인에 명시된 표준 용어 준수 여부
    3. **기술**: HTML 마크업 오류(id 중복 등), 보안 취약점(autocomplete 미설정 등), 반응형 깨짐, 웹 접근성 기술 표준(WAI-ARIA) 위반
    
    결과는 프론트엔드 대시보드에 바로 띄울 수 있도록 반드시 아래 JSON 형식으로만 출력해. 
    다른 설명이나 마크다운 기호(```json)는 절대 쓰지 마.
    만약 위반 사항이 없다면 "issues": [] 빈 리스트를 반환해.
    
    {{
        "status": "success",
        "total_checked_count": 30,
        "summary": "전체적인 분석 요약평을 2~3줄로 작성",
        "issues": [
            {{
                "category": "디자인", 
                "description": "버튼 색상이 가이드라인(p.12)과 다름", 
                "suggestion": "#003366으로 변경하세요.",
                "source_file": "gov_guide.pdf",  
                "page_number": [12]
            }},
            {{
                "category": "운영", 
                "description": "메인 배너 텍스트 가독성 부족", 
                "suggestion": "명도 대비를 4.5:1 이상으로 높이세요.",
                "source_file": "gov_guide.pdf",  
                "page_number": [12]
            }},
            {{
                "category": "기술", 
                "description": "모바일 화면에서 입력창이 영역을 벗어남", 
                "suggestion": "CSS width 속성을 100%로 수정하세요.",
                "source_file": "gov_guide.pdf",  
                "page_number": [12, 13]
            }}
        ]
    }}
    
    [HTML 소스코드 일부]
    {html_content[:5000]}
    """
    
    generation_config = genai.types.GenerationConfig(
        temperature=0.0,
        response_mime_type="application/json"
    )
    
    model = genai.GenerativeModel(
        model_name="models/gemini-2.5-flash",
        generation_config=generation_config
    )
    
    analysis_input = [image_file, prompt]
    response = model.generate_content(analysis_input)
    
    raw_text = response.text
    clean_json = raw_text.strip()
    
    try:
        result_dict = json.loads(clean_json)
        issues = result_dict.get("issues", [])
        
        for issue in issues:
            if issue.get("category") == "접근성":
                issue["category"] = "기술"
                
        d_f = len([i for i in issues if i.get("category") == "디자인"])
        o_f = len([i for i in issues if i.get("category") == "운영"])
        t_f = len([i for i in issues if i.get("category") == "기술"])

        result_dict["design_faults"] = d_f
        result_dict["ops_faults"] = o_f
        result_dict["tech_faults"] = t_f
        result_dict["total_faults"] = d_f + o_f + t_f

        result_dict["total_count"] = result_dict.get("total_checked_count", 30)

        result_dict["scores"] = {
            "디자인": calculate_score(result_dict["total_count"], d_f),
            "운영": calculate_score(result_dict["total_count"], o_f),
            "기술": calculate_score(result_dict["total_count"], t_f)
        }
        
        end_time = time.time()
        total_duration = end_time - start_time
        print(f"분석 소요 시간: {total_duration:.2f}초")
        
        return result_dict
            
    except Exception as e:
        print(f"JSON 파싱 에러: {e}")
        return {
            "status": "error", "message": str(e), "issues": [],
            "scores": {"디자인": 0, "운영": 0, "기술": 0},
            "design_faults": 0, "ops_faults": 0, "tech_faults": 0, "total_faults": 0, "total_count": 0
        }
    finally: 
        try:
            genai.delete_file(image_file.name)
        except:
            pass

def merge_results(results, tree_nodes=None):
    if not results: 
        return {
            "status": "error", "message": "분석 결과 데이터가 없습니다.", 
            "scores": {"디자인": 0, "운영": 0, "기술": 0}, "issues": [], "tree_nodes": []
        }
    
    total_checked = sum(r.get("total_count", 30) for r in results)
    d_faults = sum(r.get("design_faults", 0) for r in results)
    o_faults = sum(r.get("ops_faults", 0) for r in results)
    t_faults = sum(r.get("tech_faults", 0) for r in results)

    d_scores = [r.get("scores", {}).get("디자인", 0) for r in results]
    o_scores = [r.get("scores", {}).get("운영", 0) for r in results]
    t_scores = [r.get("scores", {}).get("기술", 0) for r in results]

    avg_d_score = sum(d_scores) // len(results)
    avg_o_score = sum(o_scores) // len(results)
    avg_t_score = sum(t_scores) // len(results)

    # 트리 노드별 결함 수 매핑
    fault_map = {r.get("url"): r.get("total_faults", 0) for r in results}
    if tree_nodes:
        for node in tree_nodes:
            node["faults"] = fault_map.get(node["id"], 0)
    
    combined = {
        "status": "success",
        "total_count": total_checked,
        "design_faults": d_faults,
        "ops_faults": o_faults,
        "tech_faults": t_faults,
        "summary": "사이트 전체 분석 결과입니다. " + results[0].get("summary", ""),
        "issues": [],
        "scores": {
            "디자인": avg_d_score,
            "운영": avg_o_score,
            "기술": avg_t_score
        },
        "tree_nodes": tree_nodes or [] # 트리 시각화 데이터 구조
    }
    
    for r in results:
        url_label = r.get("url", "알 수 없는 페이지")
        for issue in r.get("issues", []):
            issue["description"] = f"[{url_label}] {issue.get('description')}"
            combined["issues"].append(issue)
            
    return combined

async def run_analysis_flow(target_url, is_any_checked, upload_file):
    all_page_results = []
    tree_nodes = [] # 트리 구조 수집 리스트
    
    print("\n" + "="*50)
    print(f"사이트 분석 시작")
    print(f"대상 URL: {target_url}")
    print("="*50)

    async with async_playwright() as p:
        print("브라우저 실행 중")
        browser = await p.chromium.launch(headless=True) 
        context = await browser.new_context(viewport={'width': 1280, 'height': 800}) 
        
        try:
            print("메인 페이지 접속 및 하위 링크 수집 시작...")
            yield {"progress": 5, "text": "링크 수집 중..."}
            main_page = await context.new_page()
            await main_page.goto(target_url, wait_until="networkidle", timeout=30000)
            
            parsed_url = urlparse(target_url)
            base_domain = parsed_url.netloc
            
            hrefs = await main_page.evaluate("() => Array.from(document.querySelectorAll('a')).map(el => el.href)")
            
            targets = [target_url]
            # Root 노드 등록
            tree_nodes.append({
                "id": target_url, 
                "label": "메인 페이지", 
                "parent": None
            })

            for href in hrefs:
                clean_url = href.split("#")[0].rstrip("/")
                if urlparse(clean_url).netloc == base_domain and clean_url not in targets:
                    if len(targets) >= 3: break
                    targets.append(clean_url)
                    path_label = urlparse(clean_url).path or clean_url
                    tree_nodes.append({
                        "id": clean_url, 
                        "label": path_label, 
                        "parent": target_url
                    })
            
            print(f"수집 완료. 총 {len(targets)}개 페이지 분석 예정")
            yield {"progress": 15, "text": f"{len(targets)}개 페이지 분석 시작..."}
            for t in targets: print(f"   > {t}")
            await main_page.close()

            # 페이지별 분석 로직
            async def analyze_single_page(idx, url):
                page = await context.new_page()
                shot_path = os.path.abspath(f"temp_shot_{idx}.png")
                try:
                    print(f"\n [{idx+1}/{len(targets)}] 페이지 분석 시작: {url}")
                    await page.goto(url, wait_until="networkidle", timeout=30000) 
                    await asyncio.sleep(3)
                    
                    await page.screenshot(path=shot_path)
                    html_data = await page.content() 
                    
                    retry = 0
                    while not os.path.exists(shot_path) and retry < 10:
                        await asyncio.sleep(0.1)
                        retry += 1

                    res = await asyncio.to_thread(
                        analyze_with_gemini, html_data, shot_path, upload_file
                    )

                    print(f"해당 페이지 분석 완료. 점수: {res.get('scores')}")
                    res['url'] = url
                    return res
                except Exception as e:
                    print(f" {url} 분석 중 에러 발생: {e}")
                    return {"status": "error", "url": url, "issues": []}
                finally:
                    if os.path.exists(shot_path): 
                        try: os.remove(shot_path)
                        except: pass
                    await page.close()

            print("\n모든 수집된 페이지의 Gemini 분석 가동 준비 및 AI 추론을 시작")

            tasks = [asyncio.create_task(analyze_single_page(idx, url)) for idx, url in enumerate(targets)]

            complete_count = 0
            for future in asyncio.as_completed(tasks):
                result = await future
                if result.get("status") != "error":
                    all_page_results.append(result)
                complete_count += 1
                progress_count = 15 + int((complete_count / len(targets)) * 75)
                yield {"progress": min(progress_count, 90), "text": f"{complete_count}/{len(targets)} 완료..."}

            print("\n" + "="*50)
            print("모든 페이지 병렬 분석 프로세스 종료")
            print("="*50 + "\n")

            if all_page_results:
                yield {"progress": 95, "text": "페이지 분석 완료, 결과 통합 중..."}
                final_report = merge_results(all_page_results, tree_nodes)
                await asyncio.sleep(0.5)
                yield {"progress": 100, "text": "분석 완료", "result": final_report}
            else:
                yield {"progress": -1, "text": "분석 실패: 유효한 결과가 없습니다."}

        except Exception as e:
            print(f"분석 도중 에러 발생: {e}")
            yield {"progress": -1, "text": f"오류 발생: {str(e)}"}
        finally:
            print("잔여 비동기 태스크 셧다운 시작...")
            if 'tasks' in locals():
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            await browser.close()
            print("비동기 태스크 및 브라우저 정리 완료.")