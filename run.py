import asyncio
import sys
import os
import uvicorn

if sys.platform == "win32":
    sys.stderr = open(os.devnull, "w")

# Uvicorn이 켜지기 전에 윈도우 비동기 설정을 최우선으로 강제 적용
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

if __name__ == "__main__":
    # 에러를 유발하는 자식 프로세스 생성을 끄고 안전하게 실행
    uvicorn.run("main:app", host="127.0.0.1", port=8000)