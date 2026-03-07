"""
프로젝트 공통 설정 모듈
- Neo4j 연결 정보
- 인덱스/임베딩 상수
- 경로 상수
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://localhost:7687")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_AUTH = ("neo4j", NEO4J_PASSWORD)

# Google AI
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
LLM_MODEL = "gemini-3-flash-preview"
EMBEDDING_MODEL = "gemini-embedding-001"

# 벡터 인덱스
INDEX_NAME = "menu_vector_index"
QA_INDEX_NAME = "qa_vector_index"
EMBEDDING_DIM = 3072

# 경로
MANUALS_DIR = "manuals"
MENU_CSV_PATH = os.path.join(os.path.dirname(__file__), "docs", "xperp_menu_list_all.csv")
