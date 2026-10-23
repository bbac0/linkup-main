import os
import random
import json

# 1. 10가지 상이한 베이스 템플릿
BASE_TEMPLATES = {
    "portal_main": """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>정부24 메인</title></head><body><header><h1 class="title">주요 서비스</h1><a href="/login">로그인</a></header><main><div class="container"><button class="btn">신청하기</button><div id="main-banner">배너</div></div></main></body></html>""",
    "board_list": """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>공지사항 게시판</title></head><body><header><h1>공지사항 목록</h1><a href="/login">로그인</a></header><main><table class="board-table"><tr><th>번호</th><th>제목</th></tr><tr><td>1</td><td>서비스 안내</td></tr></table></main></body></html>""",
    "login_page": """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>로그인 - 대한민국 정부</title></head><body><main class="login-box"><h2>로그인</h2><input type="text" placeholder="아이디"><button class="login-btn">로그인</button></main></body></html>""",
    "detail_page": """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>민원 안내 상세</title></head><body><header><a href="/login">로그인</a></header><main><article><h2>주민등록표등본 발급</h2><p>발급 수수료 안내입니다.</p><button class="apply-btn">발급 신청</button></article></main></body></html>""",
    "dashboard": """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>마이페이지 대시보드</title></head><body><aside><ul><li><a href="/login">로그인</a></li></ul></aside><main><div class="card"><h3>신청 처리 내역</h3></div></main></body></html>""",
    "search_result": """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>통합 검색 결과</title></head><body><header><input type="text" value="민원"><a href="/login">로그인</a></header><main><ul class="results"><li>검색 결과 항목 1</li></ul></main></body></html>""",
    "faq_page": """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>자주 묻는 질문</title></head><body><header><h1>FAQ</h1></header><main><dl><dt>Q. 발급 수수료는?</dt><dd>무료입니다.</dd></dl><a href="/login">로그인</a></main></body></html>""",
    "form_page": """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>민원 신청서 작성</title></head><body><main><form><label>성명</label><input type="text"><button type="submit">제출하기</button></form></main></body></html>""",
    "terms_page": """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>이용약관</title></head><body><header><h1>이용약관</h1></header><main><p>본 약관은 정부 서비스 이용에 관한 사항을 규정합니다.</p><a href="/login">로그인</a></main></body></html>""",
    "contact_page": """<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>고객센터</title></head><body><header><h1>문의하기</h1></header><main><p>전화번호: 110</p><a href="/login">로그인</a></main></body></html>"""
}

# 2. B방법 고도화 결함 라이브러리
DEFECT_POOL = [
    {
        "id": "DEF_VIS_01", "category": "Visual", "type": "Poor Contrast Ratio",
        # 극심한 명도 대비 저하 (흰색 배경에 아주 연한 회색 글자)
        "apply": lambda h: h.replace("</head>", "<style>* { color: #e5e5e5 !important; background-color: #ffffff !important; }</style></head>")
    },
    {
        "id": "DEF_VIS_02", "category": "Visual", "type": "Mobile Layout Breakage",
        # 데스크톱 해상도에서도 화면이 옆으로 크게 터지도록 3000px 전역 강제 지정
        "apply": lambda h: h.replace("</head>", "<style>html, body, div, main, header { width: 3000px !important; min-width: 3000px !important; overflow-x: scroll !important; }</style></head>")
    },
    {
        "id": "DEF_VIS_03", "category": "Visual", "type": "Button Area Clipping",
        # 버튼 높이를 2px로 찌그러뜨리고 폰트 0으로 지워 시각적 잘림 유발
        "apply": lambda h: h.replace("</head>", "<style>button, .btn, .login-btn, .apply-btn { height: 2px !important; font-size: 0px !important; line-height: 0 !important; padding: 0 !important; overflow: hidden !important; border: 1px solid red !important; }</style></head>")
    },
    {
        "id": "DEF_OPS_01", "category": "Guideline", "type": "Standard Terminology Violation",
        "apply": lambda h: h.replace("로그인", "접속하기")
    },
    {
        "id": "DEF_TEC_01", "category": "Technical", "type": "Duplicate ID Markup Error",
        "apply": lambda h: h.replace("<body>", '<body><div id="main-banner"></div><div id="main-banner"></div>')
    }
]

def generate_dataset(total_count=200, output_dir="testbed_200"):
    os.makedirs(output_dir, exist_ok=True)
    random.seed(42)
    
    template_keys = list(BASE_TEMPLATES.keys())
    metadata_list = []
    
    for i in range(1, total_count + 1):
        tpl_name = template_keys[(i - 1) % len(template_keys)]
        current_html = BASE_TEMPLATES[tpl_name]
        
        injected_defects = []
        
        # 20%는 정상 페이지, 80%는 결함 1~2개 주입
        if random.random() > 0.2:
            defect_count = random.choice([1, 2])
            selected_defects = random.sample(DEFECT_POOL, defect_count)
            
            for defect in selected_defects:
                current_html = defect["apply"](current_html)
                injected_defects.append({
                    "id": defect["id"],
                    "category": defect["category"],
                    "type": defect["type"]
                })
        
        filename = f"page_{i:03d}.html"
        filepath = os.path.join(output_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(current_html)
            
        metadata_list.append({
            "page_id": filename,
            "base_template": tpl_name,
            "has_defect": len(injected_defects) > 0,
            "defect_count": len(injected_defects),
            "defects": injected_defects
        })
    
    with open(os.path.join(output_dir, "dataset_metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata_list, f, ensure_ascii=False, indent=2)

    print(f"✅ [B방법] 200개 테스트베드 생성 완료! (경로: ./{output_dir})")

if __name__ == "__main__":
    generate_dataset(200)