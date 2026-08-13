from playwright.async_api import async_playwright

async def capture_webpage(url: str, output_image_path: str) -> str:
    print(f"{url} 접속 및 캡처")
    async with async_playwright() as p:
        # 헤드리스 브라우저 실행
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # URL 이동 및 렌더링 대기
        await page.goto(url, wait_until="networkidle")
        
        # 스크린샷 저장
        await page.screenshot(path=output_image_path, full_page=True)
        
        # HTML 소스 추출
        html_content = await page.inner_html("body")
        await browser.close()
        
        print("캡처 및 HTML 추출 완료")
        return html_content