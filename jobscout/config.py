from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field


ROOT = Path(__file__).resolve().parents[1]


class LinkedInConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    query: str = "AI Engineer"
    location: str = "Remote"
    date_posted_hours: int = 24
    workplace: str = "remote"
    experience: str = "mid"
    max_jobs: int = 25


class IndeedConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    base_url: str = "https://www.indeed.com"
    query: str = "AI Engineer"
    location: str = "Remote"
    fromage: int = 1
    radius: int = 25
    remote: bool = True
    skip_sponsored: bool = True
    max_jobs: int = 25


class SourcesConfig(BaseModel):
    linkedin: LinkedInConfig = Field(default_factory=LinkedInConfig)
    indeed: IndeedConfig = Field(default_factory=IndeedConfig)


class BrowserConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    headless: bool = False
    channel: str = "chrome"
    page_timeout_ms: int = 45_000
    slow_mo_ms: int = 0
    profile_dir: Path = ROOT / ".browser-profiles"


class UIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class CoverLetterConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    base_url: str = "http://localhost:11434/v1"
    api_key: str | None = "ollama"
    model: str = "qwen2.5:7b"
    vision_model: str = "qwen2.5:7b"
    embed_base_url: str = "http://localhost:11434"
    embed_model: str = "bge-m3"
    num_ctx: int = 4096
    model_profile: str = "balanced"  # "fast", "balanced", "quality"
    semantic_weight: float = 0.7
    keyword_weight: float = 0.3
    match_threshold: float = 0.25
    max_matches: int = 8
    embedding_db: Path = ROOT / "data" / "embeddings.sqlite3"
    temperature: float = 0.2
    max_tokens: int = 16384
    timeout_seconds: float = 300.0
    profile_path: Path = ROOT / "config" / "profile.yaml"
    template_dir: Path = ROOT / "templates"
    output_dir: Path = ROOT / "data" / "cover_letters"
    description_dir: Path = ROOT / "data" / "job_descriptions"
    auto_fetch_descriptions: bool = True
    auto_fetch_limit: int = 10
    compile_pdf: bool = True
    detail_timeout_ms: int = 45_000
    cv_import_max_pages: int = 4
    cv_import_max_upload_mb: int = 12


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    dedupe_cross_source: bool = False
    sink: Literal["auto", "local", "google"] = "auto"
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    cover_letter: CoverLetterConfig = Field(default_factory=CoverLetterConfig)
    root: Path = ROOT
    google_sheet_id: str | None = None
    google_service_account_json: Path | None = None
    google_sheet_url: str | None = None
    local_jobs_csv: Path = ROOT / "data" / "jobs.csv"
    local_runs_csv: Path = ROOT / "data" / "runs.csv"

    def source_config(self, name: str) -> LinkedInConfig | IndeedConfig:
        if name == "linkedin":
            return self.sources.linkedin
        if name == "indeed":
            return self.sources.indeed
        raise KeyError(name)


def load_settings(path: str | Path | None = None) -> Settings:
    load_dotenv(ROOT / ".env")
    config_path = Path(path) if path else ROOT / "config" / "search.yaml"
    raw: dict = {}
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    profile_dir = Path(os.getenv("BROWSER_PROFILE_DIR", str(ROOT / ".browser-profiles")))
    if not profile_dir.is_absolute():
        profile_dir = ROOT / profile_dir

    raw.setdefault("browser", {})
    raw["browser"]["profile_dir"] = profile_dir

    raw.setdefault("ui", {})
    raw["ui"]["host"] = os.getenv("JOBSCOUT_HOST", raw["ui"].get("host", "127.0.0.1"))
    raw["ui"]["port"] = int(os.getenv("JOBSCOUT_PORT", raw["ui"].get("port", 8000)))

    sink = os.getenv("SINK", raw.get("sink", "auto")).lower()
    if sink not in {"auto", "local", "google"}:
        sink = "auto"
    raw["sink"] = sink

    service_account = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    service_account_path = Path(service_account) if service_account else None
    if service_account_path and not service_account_path.is_absolute():
        service_account_path = ROOT / service_account_path

    raw["root"] = ROOT
    raw["google_sheet_id"] = os.getenv("GOOGLE_SHEET_ID", "").strip() or None
    raw["google_service_account_json"] = service_account_path
    raw["google_sheet_url"] = os.getenv("GOOGLE_SHEET_URL", "").strip() or None
    raw["local_jobs_csv"] = ROOT / "data" / "jobs.csv"
    raw["local_runs_csv"] = ROOT / "data" / "runs.csv"

    raw.setdefault("cover_letter", {})
    cover_letter = raw["cover_letter"]
    cover_letter["base_url"] = os.getenv(
        "COVER_LETTER_BASE_URL",
        cover_letter.get("base_url", "https://integrate.api.nvidia.com/v1"),
    )
    cover_letter["api_key"] = (
        os.getenv("COVER_LETTER_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
        or os.getenv("NVIDIA_API_KEY", "").strip()
        or os.getenv("NVAPI_KEY", "").strip()
        or cover_letter.get("api_key")
    )
    cover_letter["model"] = os.getenv(
        "COVER_LETTER_MODEL",
        cover_letter.get("model", "nvidia/nemotron-3.5-lightning-30b-a3b"),
    )
    cover_letter["vision_model"] = os.getenv(
        "COVER_LETTER_VISION_MODEL",
        cover_letter.get("vision_model", "nvidia/nemotron-3.5-super-vl-120b-a12b"),
    )
    if os.getenv("COVER_LETTER_TIMEOUT"):
        cover_letter["timeout_seconds"] = float(os.getenv("COVER_LETTER_TIMEOUT", "300"))
    if os.getenv("COVER_LETTER_COMPILE_PDF"):
        cover_letter["compile_pdf"] = os.getenv("COVER_LETTER_COMPILE_PDF", "1").lower() not in {"0", "false", "no"}
    if os.getenv("CV_IMPORT_MAX_PAGES"):
        cover_letter["cv_import_max_pages"] = max(
            1, int(os.getenv("CV_IMPORT_MAX_PAGES", "4"))
        )
    if os.getenv("CV_IMPORT_MAX_UPLOAD_MB"):
        cover_letter["cv_import_max_upload_mb"] = max(
            1, int(os.getenv("CV_IMPORT_MAX_UPLOAD_MB", "12"))
        )
    if os.getenv("COVER_LETTER_EMBED_BASE_URL"):
        cover_letter["embed_base_url"] = os.getenv("COVER_LETTER_EMBED_BASE_URL")
    if os.getenv("COVER_LETTER_EMBED_MODEL"):
        cover_letter["embed_model"] = os.getenv("COVER_LETTER_EMBED_MODEL")
    if os.getenv("COVER_LETTER_NUM_CTX"):
        cover_letter["num_ctx"] = int(os.getenv("COVER_LETTER_NUM_CTX"))
    if os.getenv("COVER_LETTER_MAX_TOKENS"):
        cover_letter["max_tokens"] = int(os.getenv("COVER_LETTER_MAX_TOKENS"))
    if os.getenv("COVER_LETTER_AUTO_DESCRIPTIONS"):
        cover_letter["auto_fetch_descriptions"] = os.getenv(
            "COVER_LETTER_AUTO_DESCRIPTIONS", "1"
        ).lower() not in {"0", "false", "no"}
    if os.getenv("COVER_LETTER_AUTO_DESCRIPTION_LIMIT"):
        cover_letter["auto_fetch_limit"] = max(
            0, int(os.getenv("COVER_LETTER_AUTO_DESCRIPTION_LIMIT", "10"))
        )
    if os.getenv("COVER_LETTER_MODEL_PROFILE"):
        profile = os.getenv("COVER_LETTER_MODEL_PROFILE", "balanced").lower()
        cover_letter["model_profile"] = profile
        if "COVER_LETTER_MODEL" not in os.environ:
            profiles_map = {
                "fast": "qwen2.5:3b",
                "balanced": "qwen2.5:7b",
                "quality": "qwen2.5:14b",
            }
            if profile in profiles_map:
                cover_letter["model"] = profiles_map[profile]
                cover_letter["vision_model"] = profiles_map[profile]
    if os.getenv("COVER_LETTER_SEMANTIC_WEIGHT"):
        cover_letter["semantic_weight"] = float(os.getenv("COVER_LETTER_SEMANTIC_WEIGHT"))
    if os.getenv("COVER_LETTER_KEYWORD_WEIGHT"):
        cover_letter["keyword_weight"] = float(os.getenv("COVER_LETTER_KEYWORD_WEIGHT"))
    if os.getenv("COVER_LETTER_MATCH_THRESHOLD"):
        cover_letter["match_threshold"] = float(os.getenv("COVER_LETTER_MATCH_THRESHOLD"))
    if os.getenv("COVER_LETTER_MAX_MATCHES"):
        cover_letter["max_matches"] = int(os.getenv("COVER_LETTER_MAX_MATCHES"))
    if os.getenv("COVER_LETTER_EMBEDDING_DB"):
        db_path = Path(os.getenv("COVER_LETTER_EMBEDDING_DB"))
        cover_letter["embedding_db"] = db_path if db_path.is_absolute() else ROOT / db_path

    for key, env_key in (
        ("profile_path", "COVER_LETTER_PROFILE"),
        ("output_dir", "COVER_LETTER_OUTPUT_DIR"),
        ("description_dir", "COVER_LETTER_DESCRIPTION_DIR"),
    ):
        value = os.getenv(env_key, "").strip()
        if value:
            path = Path(value)
            cover_letter[key] = path if path.is_absolute() else ROOT / path

    return Settings.model_validate(raw)
