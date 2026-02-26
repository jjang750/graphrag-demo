"""
Gemini 네이티브 임베더 클래스
google-genai (최신) SDK를 사용하여 임베딩 생성
neo4j-graphrag의 Embedder 기본 클래스를 상속
"""

import os
from typing import List
from neo4j_graphrag.embeddings.base import Embedder

try:
    from google import genai
except ImportError:
    raise ImportError(
        "google-genai 패키지가 필요합니다.\n"
        "다음 명령으로 설치하세요: uv pip install google-genai"
    )


class GeminiEmbedder(Embedder):
    """
    Google Gemini 네이티브 API를 이용한 임베더.
    최신 google-genai SDK 사용.

    Args:
        model (str): 사용할 임베딩 모델명. 기본값 "text-embedding-004"
        api_key (str): Google AI Studio API 키. 없으면 GOOGLE_API_KEY 환경변수 사용.
    """

    def __init__(
        self,
        model: str = "gemini-embedding-001",
        api_key: str | None = None,
    ) -> None:
        self.model = model
        resolved_key = api_key or os.getenv("GOOGLE_API_KEY")
        if not resolved_key:
            raise ValueError(
                "GOOGLE_API_KEY 환경변수 또는 api_key 파라미터가 필요합니다."
            )
        # google-genai 클라이언트 초기화
        self.client = genai.Client(api_key=resolved_key)

    def embed_query(self, text: str) -> List[float]:
        """
        텍스트를 Gemini API로 임베딩하여 float 리스트 반환.

        Args:
            text (str): 임베딩할 텍스트

        Returns:
            List[float]: 임베딩 벡터
        """
        try:
            result = self.client.models.embed_content(
                model=self.model,
                contents=text,
            )
            return result.embeddings[0].values
        except Exception as e:
            raise RuntimeError(f"Gemini 임베딩 생성 실패: {e}") from e
