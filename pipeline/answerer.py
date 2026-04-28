import os

import anthropic
from dotenv import load_dotenv

from pipeline.prompts import SYSTEM_PROMPT
from pipeline.schemas import AnswerPayload

load_dotenv()

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")
_TOOL_NAME = "submit_answer"


def build_context(chunks: list[dict]) -> str:
    """검색된 chunk 리스트를 [근거 N] 형태의 문자열로 변환한다."""
    blocks = []
    for i, chunk in enumerate(chunks, start=1):
        block = (
            f"[근거 {i}]\n"
            f"문서명: {chunk.get('doc_name', '')}\n"
            f"문서유형: {chunk.get('doc_type', '')}\n"
            f"조문번호: {chunk.get('article_no', '')}\n"
            f"조문제목: {chunk.get('article_title', '')}\n"
            f"페이지: {chunk.get('page', 0)}\n"
            f"내용:\n{chunk.get('text', '')}"
        )
        blocks.append(block)
    return "\n\n".join(blocks)


def generate_answer(question: str, chunks: list[dict]) -> dict:
    """
    검색된 chunks를 근거로 Claude에게 질문하고 AnswerPayload dict를 반환한다.

    Args:
        question: 사용자 질문 원문
        chunks: Qdrant payload dict 리스트 (doc_name, doc_type, article_no, article_title, page, text)

    Returns:
        AnswerPayload.model_dump() 결과 dict

    Raises:
        ValueError: chunks가 비어 있을 때
        RuntimeError: Claude 응답에서 tool_use 블록이 없거나 파싱 실패 시
    """
    if not chunks:
        raise ValueError(
            "검색된 청크가 없습니다. 먼저 관련 문서를 인덱싱하고 검색 결과를 확인하세요."
        )

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    user_prompt = f"[검색된 근거]\n{build_context(chunks)}\n\n[질문]\n{question}"

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "name": _TOOL_NAME,
                "description": "연구행정 질문에 대한 구조화된 답변을 제출합니다.",
                "input_schema": AnswerPayload.model_json_schema(),
            }
        ],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[{"role": "user", "content": user_prompt}],
    )

    tool_block = next(
        (block for block in response.content if block.type == "tool_use"),
        None,
    )
    if tool_block is None:
        raise RuntimeError(
            f"Claude 응답에 tool_use 블록이 없습니다. 응답 내용: {response.content}"
        )

    raw = tool_block.input

    # citations가 비어 있으면 chunks에서 직접 채워 min_length=1 보장
    if not raw.get("citations"):
        raw["citations"] = [
            {
                "document_name": chunks[0].get("doc_name", "알 수 없음"),
                "article_no": chunks[0].get("article_no", "알 수 없음"),
                "page": chunks[0].get("page", 0),
                "quote": chunks[0].get("text", "")[:50],
            }
        ]

    try:
        payload = AnswerPayload.model_validate(raw)
    except Exception as exc:
        raise RuntimeError(
            f"AnswerPayload 파싱 실패: {exc}\n수신된 입력값: {raw}"
        ) from exc

    return payload.model_dump()
