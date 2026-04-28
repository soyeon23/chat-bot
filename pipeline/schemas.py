from typing import Literal
from pydantic import BaseModel, Field

VerdictType = Literal["가능", "불가", "조건부 가능", "판단불가"]


class Citation(BaseModel):
    document_name: str = Field(description="출처 문서명 (예: 국가연구개발혁신법)")
    article_no: str = Field(description="조문 번호 (예: 제13조 제2항)")
    page: int = Field(description="해당 조문의 PDF 페이지 번호")
    quote: str = Field(description="근거로 사용된 원문 발췌 (50자 이내)")


class AnswerPayload(BaseModel):
    verdict: VerdictType = Field(
        description="최종 판단 결과: 가능 / 불가 / 조건부 가능 / 판단불가"
    )
    summary: str = Field(
        description="한 줄 결론 요약"
    )
    citations: list[Citation] = Field(
        min_length=1,
        description="판단 근거가 되는 조문 목록. 반드시 1개 이상 포함해야 한다. 판단불가인 경우에도 검토한 문서 중 가장 관련 있는 조문을 인용한다.",
    )
    follow_up_needed: bool = Field(
        description="전담기관 또는 담당 PM의 추가 확인이 필요한 경우 true"
    )
    follow_up_questions: list[str] = Field(
        description="추가 확인이 필요한 사항 목록. follow_up_needed가 false이면 빈 배열."
    )
    risk_notes: list[str] = Field(
        description="집행 시 주의해야 할 리스크 또는 조건 목록. 없으면 빈 배열."
    )
