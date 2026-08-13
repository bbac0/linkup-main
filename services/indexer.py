import os
import time
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma 
from dotenv import load_dotenv

load_dotenv()

def create_vector_db():
    current_dir = os.path.dirname(os.path.abspath(__file__)) 
    parent_dir = os.path.dirname(current_dir) 

    pdf_files = [
        os.path.join(parent_dir, "data", "gov_check.pdf"),
        os.path.join(parent_dir, "data", "gov_guide.pdf")
    ]
    
    all_splits = []
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
    
    for pdf_path in pdf_files:
        if os.path.exists(pdf_path):
            print(f"{os.path.basename(pdf_path)} 읽는 중...")
            try:
                loader = PyPDFLoader(pdf_path)
                pages = loader.load()
                splits = text_splitter.split_documents(pages)
                
                valid_splits = [s for s in splits if s.page_content.strip()]
                all_splits.extend(valid_splits)
            except Exception as e:
                print(f"{pdf_path} 읽기 실패: {e}")
        else:
            print(f"파일을 찾을 수 없습니다: {pdf_path}")

    if not all_splits:
        print("경고: 입력된 데이터가 없어 DB를 생성하지 못했습니다.")
        return

    print(f"총 {len(all_splits)}개의 유효한 텍스트 조각을 찾았습니다")
    print("벡터 DB 생성 중")
    
    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/gemini-embedding-001",
        task_type="retrieval_document" 
    )
    
    vector_store = Chroma(
        embedding_function=embeddings, 
        persist_directory="./chroma_db"
    )
    
    batch_size = 10
    
    for i in range(0, len(all_splits), batch_size):
        batch = all_splits[i : i + batch_size]
        try:
            vector_store.add_documents(batch)
            # 진행률이 몇 개까지 완료되었는지 정확히 표시
            current_count = min(i + batch_size, len(all_splits))
            print(f"진행 상황: {current_count} / {len(all_splits)} 청크 완료")
            
            time.sleep(2) 
        except Exception as e:
            print(f"에러 발생: {e}")
            print("10초 대기")
            time.sleep(10)
            
    print("성공 ./chroma_db 폴더에 가이드라인 지식이 모두 저장되었습니다.")

if __name__ == "__main__":
    create_vector_db()