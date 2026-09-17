"""`.env.example` 的两条诚实性检查:列出的键有人读、必需的键在列。

模板是靠手抄长起来的(每加一个旋钮要在 settings.py、.env.example、
docker-compose.yml 三处各写一遍),所以它需要一个廉价的守卫:死条目和
漏掉的必填项都在这里失败,而不是在下一次部署时才发现。
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic_settings import BaseSettings

from config.settings import InfraSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / ".env.example"

# 键既可以写成生效行,也可以写成注释掉的示例行——两种都是"模板列出了它"。
_KEY_LINE = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=")
_TOKEN = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")

# 消费者 = 代码与部署配置。文档里的提及不算"有人读",否则死条目只要被
# 写进 config-reference.md 就能蒙混过关。
_CONSUMER_GLOBS = (
    "config/**/*.py",
    "src/**/*.py",
    "src/**/*.ts",
    "src/**/*.tsx",
    "src/**/*.mjs",
    "docker-compose.yml",
    "Containerfile",
    "cicd/**/*.sh",
)
_SKIPPED_DIRS = {"node_modules", "dist", "__pycache__"}

# 仓库外工具的输入:文件里查不到,但确实是部署在用的。
_EXTERNAL_CONSUMERS = {
    "COMPOSE_PROFILES": "docker compose CLI(仓库外)读取的 profile 选择",
}

# 少了它该部署就跑不起来、或跑到别的地方去的值。
REQUIRED_DEPLOYMENT_KEYS = frozenset(
    {
        "POSTGRES_PASSWORD",
        "NEO4J_PASSWORD",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_MODEL",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_API_KEY",
    }
)

# 模板是"要选什么"的清单,不是手册;旋钮默认值在 config/app.yaml。
MAX_TEMPLATE_LINES = 60


def _template_keys(template: Path = TEMPLATE) -> set[str]:
    return {
        match.group(1)
        for line in template.read_text(encoding="utf-8").splitlines()
        if (match := _KEY_LINE.match(line))
    }


def _settings_env_names(model: type[BaseSettings]) -> set[str]:
    """pydantic-settings 会读的环境名:前缀 + 字段名,子模型各带自己的前缀。"""
    prefix = model.model_config.get("env_prefix") or ""
    names: set[str] = set()
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseSettings):
            names |= _settings_env_names(annotation)
        else:
            names.add(f"{prefix}{name}".upper())
    return names


def _consumer_names() -> set[str]:
    names = _settings_env_names(InfraSettings) | set(_EXTERNAL_CONSUMERS)
    for pattern in _CONSUMER_GLOBS:
        for path in REPO_ROOT.glob(pattern):
            if _SKIPPED_DIRS & set(path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            names |= set(_TOKEN.findall(text))
    return names


def _unconsumed(template: Path = TEMPLATE) -> list[str]:
    return sorted(_template_keys(template) - _consumer_names())


def _missing_required(template: Path = TEMPLATE) -> list[str]:
    return sorted(REQUIRED_DEPLOYMENT_KEYS - _template_keys(template))


def _copy(template: Path, tmp_path: Path, extra: str = "", drop: str = "") -> Path:
    lines = [
        line
        for line in template.read_text(encoding="utf-8").splitlines()
        if not (drop and line.startswith(f"{drop}="))
    ]
    if extra:
        lines.append(extra)
    copy = tmp_path / ".env.example"
    copy.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return copy


# ── 4.2 模板里的每个键都有人读 ────────────────────────────────────────


def test_template_has_no_unconsumed_keys():
    assert _unconsumed() == [], (
        f".env.example 里有没人读的键: {_unconsumed()}"
    )


def test_a_fabricated_key_is_reported_as_unconsumed(tmp_path):
    """守卫本身要会失败,否则它只是装饰。"""
    fake = _copy(TEMPLATE, tmp_path, extra="TKB_FABRICATED_KEY=1")
    assert _unconsumed(fake) == ["TKB_FABRICATED_KEY"]


def test_template_stays_a_choice_list():
    lines = TEMPLATE.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= MAX_TEMPLATE_LINES, (
        f".env.example 有 {len(lines)} 行,超过 {MAX_TEMPLATE_LINES} 行;"
        "行为默认值应放 config/app.yaml,不要回填模板"
    )


# ── 4.3 部署必需的值都在 ──────────────────────────────────────────────


def test_required_deployment_keys_are_present():
    assert _missing_required() == [], (
        f".env.example 缺必需项: {_missing_required()}"
    )


def test_removed_required_key_is_reported(tmp_path):
    stripped = _copy(TEMPLATE, tmp_path, drop="POSTGRES_PASSWORD")
    assert "POSTGRES_PASSWORD" in _missing_required(stripped)
