"""review_ui.bp — 按业务域拆分的蓝图(Phase 1)

app.py 一次注册全部蓝图。各子模块里变量统一叫 bp,
这里按域重新导出成 xxx_bp,便于 app.py 显式 import。

蓝图:outline / chapter / entities / projects / review /
      comments / notifications / llm_config / pipeline
"""
from .outline import bp as outline_bp
from .chapter import bp as chapter_bp
from .entities import bp as entities_bp
from .projects import bp as projects_bp
from .review import bp as review_bp
from .comments import bp as comments_bp
from .notifications import bp as notifications_bp
from .llm_config import bp as llm_config_bp
from .pipeline import bp as pipeline_bp

__all__ = [
    "outline_bp", "chapter_bp", "entities_bp", "projects_bp", "review_bp",
    "comments_bp", "notifications_bp", "llm_config_bp", "pipeline_bp",
]
