from abc import ABC, abstractmethod
from pathlib import Path


class BaseExtractor(ABC):
    """文件提取器抽象基类。子类实现 extract() 将文件转为纯文本。"""

    @abstractmethod
    def extract(self, file_path: Path) -> str:
        """从文件中提取纯文本内容。

        Args:
            file_path: 文件路径

        Returns:
            提取的纯文本

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 文件格式无法解析
        """
        ...

    @staticmethod
    def _ensure_file_exists(file_path: Path) -> None:
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
