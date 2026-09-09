"""
Configuration management for ReconcileX.
Supports environment variables, .env files, and YAML overrides.
"""

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Global configuration settings for ReconcileX."""

    # LLM Settings
    llm_provider: str = Field(default="local", alias="RECONCILEX_LLM_PROVIDER")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen2.5:7b", alias="OLLAMA_MODEL")
    ollama_vision_model: str = Field(default="qwen2.5vl:7b", alias="OLLAMA_VISION_MODEL")

    # Cloud API Keys (Optional Fallbacks)
    gemini_api_key: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")
    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")

    # OCR Settings
    ocr_engine: str = Field(default="auto", alias="RECONCILEX_OCR_ENGINE")

    # Deterministic Matching Parameters
    date_tolerance_days: int = Field(default=3, alias="RECONCILEX_DATE_TOLERANCE_DAYS")
    relaxed_date_tolerance_days: int = Field(default=7, alias="RECONCILEX_RELAXED_DATE_TOLERANCE_DAYS")
    vendor_similarity_threshold: float = Field(default=75.0, alias="RECONCILEX_VENDOR_SIMILARITY_THRESHOLD")
    fee_tolerance_amount: float = Field(default=25.0, alias="RECONCILEX_FEE_TOLERANCE_AMOUNT")
    max_bundle_combinations: int = Field(default=4, alias="RECONCILEX_MAX_BUNDLE_COMBINATIONS")
    bundle_date_window_days: int = Field(default=15, alias="RECONCILEX_BUNDLE_DATE_WINDOW_DAYS")

    # Server Settings
    host: str = Field(default="127.0.0.1", alias="RECONCILEX_HOST")
    port: int = Field(default=8585, alias="RECONCILEX_PORT")
    data_dir: Path = Field(default=Path("./data"), alias="RECONCILEX_DATA_DIR")
    log_level: str = Field(default="INFO", alias="RECONCILEX_LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
