"""图片提取器：通过 VLM (视觉语言模型) 提取图片内容。

使用 DashScope qwen-vl-plus 模型，通过 OpenAI 兼容 API 调用，
将图片内容（文字、图表、图示等）提取为结构化文本。
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx
import yaml

from src.db.config import settings
from src.pipeline.extractors.base import BaseExtractor

logger = logging.getLogger(__name__)

_MIME_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "model_config.yaml"

_VLM_PROMPT = """请详细分析这张图片，提取其中的所有内容：

1. 提取图片中的所有文字（包括标题、正文、标注、表格内容等）
2. 描述图片中的图表、图示、流程图、架构图等视觉元素
3. 如果是截图或文档扫描件，保留原始结构

请以 markdown 格式返回结果，保持内容的完整性和结构。"""


def _load_vlm_config() -> dict:
    """从 model_config.yaml 加载 VLM 配置。"""
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
            return config.get("vlm", {})
    return {}


class ImageExtractor(BaseExtractor):
    """通过 VLM (视觉语言模型) 提取图片内容。

    使用 DashScope qwen-vl-plus 模型，支持提取图片中的文字、
    描述图表/图示等视觉元素，返回结构化 markdown 文本。
    """

    SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}

    def __init__(self) -> None:
        self._config = _load_vlm_config()

    def extract(self, file_path: Path) -> str:
        self._ensure_file_exists(file_path)
        try:
            # 读取图片并转为 base64 data URL
            image_bytes = file_path.read_bytes()
            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            mime_type = _MIME_MAP.get(file_path.suffix.lower(), "image/png")
            data_url = f"data:{mime_type};base64,{b64_data}"

            # 获取配置
            base_url = self._config.get(
                "base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ).rstrip("/")
            model = self._config.get("model", "qwen-vl-plus")
            api_key = self._config.get("api_key", "") or settings.llm_api_key

            if not api_key:
                raise ValueError("VLM API Key 未配置，请在 .env 中设置 LLM_API_KEY")

            # 调用 VLM API
            logger.info(f"VLM 图片提取开始: {file_path.name}, model={model}")

            with httpx.Client(timeout=120.0) as client:
                resp = client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": data_url},
                                    },
                                    {
                                        "type": "text",
                                        "text": _VLM_PROMPT,
                                    },
                                ],
                            }
                        ],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                result = data["choices"][0]["message"]["content"]

            logger.info(f"VLM 图片提取完成: {file_path.name}, {len(result)} 字符")
            return result

        except httpx.HTTPStatusError as e:
            error_body = e.response.text[:300] if e.response else ""
            raise ValueError(
                f"VLM API 调用失败: {e.response.status_code} — {error_body}"
            ) from e
        except Exception as e:
            raise ValueError(f"图片 VLM 提取失败: {file_path} — {e}") from e
