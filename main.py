#실시간으로 안돌아가서 하위 페이지 진행률이 안뜸
#비동기화 시킴

import asyncio
import sys
from fastapi import FastAPI, Form, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from typing import Optional
import shutil
import os
import json
import services.analyzer as analyzer 
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# 윈도우 비동기 설정
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# 환경 변수 로드
load_dotenv()

app = FastAPI(title="WebGuardian AI API")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")

app.mount("/pdfs", StaticFiles(directory=DATA_DIR), name="pdfs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("temp", exist_ok=True)

@app.post("/api/analyze")
async def analyze_url(
    url: str = Form(...),
    guide_type: str = Form(...),
    guide_file: Optional[UploadFile] = File(None)
):
    try:
        # 가이드라인 PDF 경로 결정
        selected_types = [t.strip() for t in guide_type.split(",") if t.strip()]
        target_pdf_paths = []
        
        for g_type in selected_types:
            if g_type == "custom" and guide_file is not None:
                path = f"temp/{guide_file.filename}"
                with open(path, "wb") as buffer:
                    shutil.copyfileobj(guide_file.file, buffer)
                target_pdf_paths.append(path)
            elif g_type == "gov_guide":
                target_pdf_paths.append("data/gov_guide.pdf")
            elif g_type == "gov_check":
                target_pdf_paths.append("data/gov_check.pdf")
        
        if not target_pdf_paths:
            return {"message": "에러 발생", "error": "선택된 가이드라인이 없습니다."}

        # 파일 존재 여부 검증
        for path in target_pdf_paths:
            if not os.path.exists(path):
                return {"message": "에러 발생", "error": f"가이드라인 문서({path})를 찾을 수 없습니다."}
        
        # 전수 분석 실행
        print(f"DEBUG: 전수 분석 시작 - {url}")

        async def event_generator():
            try:
                async for step in analyzer.run_analysis_flow(url, target_pdf_paths, target_pdf_paths):
                    yield f"data: {json.dumps(step, ensure_ascii=False)}\n\n"
            except Exception as inner_e:
                error_step = {"progress": -1, "text": f"프로세스 내부 에러: {str(inner_e)}"}
                yield f"data: {json.dumps(error_step, ensure_ascii=False)}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")
        
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return {"message": "에러 발생", "error": str(e)}