# python run.py
# 위 명령어로 서버 실행한 뒤에 터미널 하나 더 생성
# streamlit run frontend/app.py
# 위 명령어로 프론트엔드 실행

import streamlit as st  # 파이썬 기반 웹 인터페이스 제작
import client  # 백엔드 호출
import chart    # 도넛차트 생성
import components as cp # 가이드라인 선택 체크박스

st.set_page_config(
    page_title="LinkUp",
    layout="centered"
)
with open("frontend/style.css", "r", encoding="utf-8") as f:
    st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

def start_new_analysis():
    st.session_state.show_result = False
    st.session_state.selected_category = None
    if "analysis_result" in st.session_state: # 이전 분석 결과 삭제
        del st.session_state.analysis_result

# 세션 상태 초기화
if "is_loading" not in st.session_state:    # 로딩 상태
    st.session_state.is_loading = False
if "gov_guide" not in st.session_state:     # 정부 가이드라인
    st.session_state.gov_guide = True
if "gov_check" not in st.session_state:     # 정부 체크리스트
    st.session_state.gov_check = False
if "custom_guide" not in st.session_state:  # 직접 추가
    st.session_state.custom_guide = False
if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0
if 'show_result' not in st.session_state:
    st.session_state.show_result = False

# 타이틀
st.markdown("""<h1 class="main-title">LinkUp : AI Web Guardian</h1>""", unsafe_allow_html=True)
with st.expander("💡 LinkUp", expanded=False):
    st.markdown("""
    - **LinkUp**은 웹 표준 및 가이드라인 자동 검수 시스템입니다.
    - 메인 사이트 URL 입력 시, 디지털 정부 가이드라인과 체크리스트를 기반으로 웹사이트의 디자인, 운영, 기술적 측면을 분석하여 위반 사항과 개선 제안을 제공합니다.
    - **직접 추가 기능**을 통해 자체 가이드라인을 **PDF 파일**로 업로드하여 분석에 활용할 수 있습니다.
    - 분석 결과는 각 영역별 점수와 위반 사항, 개선 제안을 포함한 상세 리포트로 제공됩니다.
    """)

col_url, col_btn = st.columns([8.5, 1.5], gap="xsmall")

# URL 입력 창
with col_url:
    target_url = st.text_input(
        label = "URL 입력",
        placeholder = "https://plus.gov.kr/",
        autocomplete = "url",
        label_visibility = "collapsed",
        icon="🔗"
    )

with col_btn:
    analyze_start = st.button(
        "분석 시작",
        width = "stretch",
        disabled = st.session_state.get("is_loading", False),
        type = "primary",
        on_click = start_new_analysis
    )

# 가이드라인 체크박스
with st.container():
    upload_file = cp.render_guide_selection()
    
# 분석 시작
if analyze_start:
    is_any_checked = cp.get_checked_guides()

    with st.spinner(" "):
        glow_status = st.empty()
        progress_bar = st.progress(0, text="분석 준비 중...")

        # 서버로 분석 요청 보내고 진행 상황 업데이트
        for step in client.run_analysis_flow(target_url, is_any_checked, upload_file):
            current_progress = step.get("progress", 0)
            progress_text = step.get("text", "처리 중...")

            # 에러 발생 시 -1로 전달
            if current_progress == -1:
                st.error(progress_text)
                progress_bar.empty()
                glow_status.empty()
                break
        
            glow_status.markdown(
                f'<span class="spinner-text-wrapper">{progress_text}</span>',
                unsafe_allow_html=True,
            )
            progress_bar.progress(
                current_progress, text=f"진행률: {current_progress}%"
            )

            if "result" in step:
                st.session_state.analysis_result = step["result"]
                progress_bar.empty()
                glow_status.empty()
                st.rerun()

# 분석 결과 보기
if "analysis_result" in st.session_state:
    # 결과 원본 확인용
    # st.subheader("데이터 디버깅")
    # st.json(st.session_state.analysis_result)
    
    res = st.session_state.analysis_result # 세션에서 가져오기
    st.session_state.show_result = True
    total = res.get("total_count", 30)

    # 분석 점수
    scores = res.get("scores", {})
    d_score = scores.get("디자인", 0)
    o_score = scores.get("운영", 0)
    t_score = scores.get("기술", 0)
    avg_score = (d_score + o_score + t_score) // 3
    
    st.divider()

    st.markdown("### 분석 리포트")
    
    summary_text = res.get("summary", "분석 결과 요약 데이터가 존재하지 않습니다.")
    
    col_chart, col_text = st.columns([3, 7], gap="large")

    with col_chart:
        chart.make_donut("평균 점수", avg_score)
        st.caption(f"전체 분석 항목 : {total}개")
    with col_text:
        st.markdown(f"""
            <p class="summary_text">{summary_text}</p>
        """, unsafe_allow_html=True)
        
    if d_score == 100 and o_score == 100 and t_score == 100:
        st.balloons()
        st.success("검토된 모든 항목이 완벽한 점수로 통과되었습니다! 🎉")

    st.divider()
    
    fields = [
        {"title": "디자인", "fault_key": "design_faults", "desc": "가이드라인 위반"},
        {"title": "운영", "fault_key": "ops_faults", "desc": "콘텐츠 및 운영 관리 위반"},
        {"title": "기술", "fault_key": "tech_faults", "desc": "기술적 무결성 위반"}
    ]

    cols = st.columns(3)

    for i, field in enumerate(fields):
        with cols[i]:
            title = field["title"]
            count = res.get(field["fault_key"], 0)
            score = res.get("scores", {}).get(title, 0)
            chart.draw_analysis_card(title, count, score, field["desc"])
    
    category = st.session_state.selected_category

    SOURCE_MAP = {
        "gov_guide.pdf": "디지털 정부 UI/UX 가이드라인",
        "gov_check.pdf": "디지털 정부 UX/UX 자체 검증 체크리스트"
    }

    all_issues = res.get("issues", [])

    design_tab, ops_tab, tech_tab = st.tabs(["디자인", "운영", "기술"])
    
    def render_issue(tab_name, category_name):
        with tab_name:
            target_issues = [i for i in all_issues if i.get("category") == category_name]
            with st.container(height=500, border=False):
                if target_issues:
                    for idx, issue in enumerate(target_issues):
                        with st.container(border = True):
                            source = issue.get("source_file")
                            is_source_valid = False

                            page = issue.get("page_number")
                            source_name = SOURCE_MAP.get(source)
                            
                            st.markdown(f"{issue.get("description")}")
                            st.info(f"{issue.get("suggestion")}")
                            if source and str(source).strip().upper() not in ["", "NONE", "NULL", "N/A"]:
                                source_name = SOURCE_MAP.get(source, "")
                                if source_name:
                                    st.caption(f"출처: {source_name}")
                                is_source_valid = True

                            if is_source_valid and page and str(page).strip().upper() != "N/A" and str(source).strip().upper() != "N/A":
                                tag_links = []
                                for p in page:
                                    if source and page:
                                        pdf_url = f"http://127.0.0.1:8000/pdfs/{source}#page={p}"
                                        
                                        tag_html = f'<a href="{pdf_url}" target="_blank" class="pdf-tag-btn" style="text-decoration: none;">{p}↗</a>'
                                        tag_links.append(tag_html)
                                
                                st.markdown("".join(tag_links), unsafe_allow_html=True)
                else:
                    st.success(f"{category_name} 영역에서 발견된 위반 사항이 없습니다!")
    render_issue(design_tab, "디자인")
    render_issue(ops_tab, "운영")
    render_issue(tech_tab, "기술")