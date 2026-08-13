import streamlit as st
import json
import requests

def run_analysis_flow(target_url, checked_types, upload_file):
    if not target_url:
        st.warning("분석할 페이지의 URL을 입력해 주세요.")
        return
    if not checked_types:
        st.warning("분석을 시작하려면 가이드라인을 최소 하나 이상 선택해주세요.")
        return
    if "custom" in checked_types and upload_file is None:
        st.error("분석할 가이드라인을 추가해 주세요.")
        return
    
    st.session_state.is_loading = True
    backend_url = "http://127.0.0.1:8000/api/analyze"
    
    # 페이로드 구성
    payload = {
        "url": target_url,
        "guide_type": ",".join(checked_types)
    }
    
    # 파일 구성
    files = None
    if upload_file:
        files = {"guide_file": (upload_file.name, upload_file.getvalue(), "application/pdf")}
    
    try:
        with requests.post(backend_url, data=payload, files=files, timeout=300, stream=True) as response:
            if response.status_code == 200:
                for line in response.iter_lines():
                    if line:
                        decoded_line = line.decode('utf-8')
                        if decoded_line.startswith("data:"):
                            json_data = decoded_line.replace("data:", "").strip()
                            result = json.loads(json_data)
                            yield result
            elif response.status_code == 429:
                st.error("할당량 초과! 잠시후 다시 시도해 주세요.")
            else:
                st.error(f"서버 에러: {response.status_code}")    
    except Exception as e:
        st.warning(f"에러 발생: {e}")
    finally:
        st.session_state.is_loading = False # 로딩 해제