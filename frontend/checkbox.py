import streamlit as st

def get_checked_guides():
    # 가이드라인 복수 처리
    checked_types = []
    if st.session_state.get("gov_guide"): checked_types.append("gov_guide")
    if st.session_state.get("gov_check"): checked_types.append("gov_check")
    if st.session_state.get("custom_guide"): checked_types.append("custom")
    return checked_types

# 정부 가이드/체크리스트 선택 시 직접 추가 해제
def _handle_gov_change():
    if st.session_state.gov_guide or st.session_state.gov_check:
        st.session_state.custom_guide = False
        
# 직접 추가 선택 시 정부 가이드/체크리스트 해제
def _handle_custom_change():
    if st.session_state.custom_guide:
        st.session_state.gov_guide = False
        st.session_state.gov_check = False
    else:
        # 직접 추가 해제 시 key 값을 1 증가시켜 위젯 새로 고침(파일 삭제 효과)
        st.session_state.uploader_key += 1

def render_guide_selection():
    st.markdown("**분석에 사용할 가이드라인을 선택해 주세요.**")

    gov_guide = st.checkbox(
        "디지털 정부 UI/UX 가이드라인",
        key = "gov_guide",
        on_change = _handle_gov_change
    )

    gov_check = st.checkbox(
        "디지털 정부 UI/UX 자체 검증 체크리스트",
        key = "gov_check",
        on_change = _handle_gov_change
    )

    # 디지털 정부 외의 가이드일 경우
    custom_guide = st.checkbox(
        "직접 추가",
        on_change = _handle_custom_change,
        key = "custom_guide"
    )

    upload_file = None

    if st.session_state.custom_guide:
        st.divider()
        upload_file = st.file_uploader(
            "파일 선택",
            type = ['pdf'],
            key=f"user_upload_{st.session_state.uploader_key}")
        st.caption("※ PDF 형식의 가이드라인만 지원합니다.")
        st.divider()