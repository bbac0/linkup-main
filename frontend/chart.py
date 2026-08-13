import streamlit as st
import pandas as pd
import altair as alt    # 그래프 생성

# 도넛 차트 그리는 함수
def make_donut(field_name, result_value):
    try:
        result_value = int(max(0, min(100, round(result_value))))
    except:
        result_value = 0
    
    if result_value >= 90:
        chart_color = '#2ECC71' # 초록
    elif result_value >= 70:
        chart_color = '#F1C40F' # 노랑
    else:
        chart_color = '#E74C3C' # 빨강
    
    source = pd.DataFrame({
        "분야" : [field_name],
        "점수" : [result_value]
    })
    
    # 데이터 고리
    plot = alt.Chart(source).mark_arc(
        innerRadius = 38, 
        outerRadius = 55, 
        cornerRadius = 10, 
    ).encode(
        color = alt.Color("분야:N",
                        scale = alt.Scale(
                            domain = [field_name],
                            range = [chart_color, "transparent"]), 
                        legend = None),
        order=alt.Order("분야:N", sort = "ascending"),
        theta = alt.Theta("점수:Q", stack = True),
        tooltip = alt.value(None)
    )

    # 배경 고리
    plot_bg = alt.Chart(pd.DataFrame({"v": [100]})).mark_arc(
        innerRadius = 38,   
        outerRadius = 55,   
        color = '#F0F4F8'
    ).encode(
        theta = alt.Theta("v:Q"),
        tooltip = alt.value(None) 
    )
    
    # 중앙 텍스트
    text = alt.Chart(pd.DataFrame({'t': [f'{result_value}%']})).mark_text(
        align = 'center',
        baseline = 'middle',
        color = chart_color,
        fontSize = 22,
        fontWeight = 700
    ).encode(
        text='t:N',
        tooltip = alt.value(None)
    )
    
    chart = (plot_bg + plot + text).properties(
        width=130, 
        height=130
    ).configure_view(   # 배경 투명하게
        strokeOpacity=0
    )
    
    st.altair_chart(
        chart, 
        width='stretch', 
        theme=None
    )

# 카드 렌더링 함수
def draw_analysis_card(title, count, score, caption_text):
    with st.container(border = False):
        st.markdown(f"""<p class="card-title">{title}</p>""", unsafe_allow_html=True)

        # 도넛 차트 그리기
        make_donut(title, score)

        # 캡션 출력
        st.caption(f"{caption_text} {count}건")