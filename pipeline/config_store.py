"""프로젝트 설정 영구 저장.

사용자가 위저드에서 입력한 PDF/HWP 경로, MCP URL, 온보딩 완료 여부 등을
`data/config.json`에 저장한다. 환경변수(.env)와 별개의 사용자 레벨 설정.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

_CONFIG_PATH = Path("data/config.json")

# 환경설정 페이지가 노출하는 모델 ID 의 단일 진실 (빈 문자열 = "(자동)" 모드).
# load_config 가 저장된 값이 이 집합 밖이면 "(자동)" 으로 자동 정리한다 —
# 옛 alias (claude-haiku-4-5-20251001, claude-sonnet-4-5 등) 가 caches·답변에
# 누출되지 않도록 부팅 시점에 1회 cleanup.
_KNOWN_MODELS: set[str] = {"", "claude-sonnet-4-6", "claude-opus-4-6"}


def _migrate_model(value: str) -> str:
    """옛/미지원 모델 ID 를 '(자동)' 빈 문자열로 reset."""
    return value if value in _KNOWN_MODELS else ""


@dataclass
class ProjectConfig:
    pdf_dir: str = "."
    hwp_dir: str = ""
    korean_law_mcp_url: str = "https://korean-law-mcp.fly.dev/mcp"
    korean_law_oc: str = ""
    hwp_mcp_enabled: bool = False
    onboarding_completed: bool = False
    last_index_count: int = 0
    # 답변 생성에 사용할 모델 — 런타임에 사이드바에서 변경 가능.
    # 비워두면 환경변수 CLAUDE_MODEL → 기본값(sonnet) 순으로 폴백.
    # 빈 문자열 = "(자동) Sonnet 기본 + comparison 만 Opus escalate" 모드.
    claude_model: str = ""
    # 비교형(여러 문서/조문 비교, 변경점 추출) 질의가 들어오면 자동으로 Opus 4.6 으로 승격.
    # claude_model 이 비어 있을 때만 의미가 있다 (사용자 명시 선택은 항상 우선).
    enable_comparison_escalate: bool = True
    # 앱 시작 시 자동으로 sync(증분 동기화) 를 한 번 돌릴지 여부.
    # ON 이어도 변경 없으면 0초로 끝나므로 부담 거의 없음.
    auto_sync_on_start: bool = False


def load_config() -> ProjectConfig:
    if not _CONFIG_PATH.exists():
        return ProjectConfig()
    try:
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ProjectConfig()
    # 기본값 + 저장된 값 병합 (필드 추가에 강하게)
    defaults = asdict(ProjectConfig())
    defaults.update({k: v for k, v in raw.items() if k in defaults})
    cfg = ProjectConfig(**defaults)

    # 옛 모델 alias 자동 정리 — 변경 있으면 디스크에도 즉시 반영.
    migrated = _migrate_model(cfg.claude_model)
    if migrated != cfg.claude_model:
        cfg.claude_model = migrated
        try:
            save_config(cfg)
        except OSError:
            pass  # 정리 실패는 다음 부팅 때 재시도

    return cfg


def save_config(cfg: ProjectConfig) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(
        json.dumps(asdict(cfg), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def update_config(**kwargs) -> ProjectConfig:
    """기존 설정에 부분 업데이트하고 저장."""
    cfg = load_config()
    for k, v in kwargs.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
    save_config(cfg)
    return cfg
