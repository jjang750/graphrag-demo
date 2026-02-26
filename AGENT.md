패키지 관리는 uv를 사용합니다.

```
uv pip install -r requirements.txt
```

파이썬 버전은 3.11을 사용합니다.
파이썬 실행은 .venv\Scripts\python.exe를 사용합니다.

서버 실행:
```
.venv\Scripts\python.exe app.py
```

API 호출:
```
curl -X POST "http://localhost:8800/query" -H "Content-Type: application/json" -d '{"question": "질문을 입력하세요."}'
```

LLM은 gemini-3-flash-preview를 사용합니다.
Embedding은 gemini-embedding-001을 사용합니다.
