# review_ui package marker — 让 review_ui/ 成为真正 Python package,
# 而不是 namespace package (PEP 420).
#
# 这样:
# - pytest 在 novel_workflow/ 跑, sys.path 含 ROOT → 'from review_ui import app' 走正常 package 路径
# - novel.py cmd_serve 用 importlib.import_module('review_ui.app') 也走 package
# - app.py 内 'from .dashboard import dashboard_bp' (相对导入) 能解析到 review_ui.dashboard
# - scripts/_start_review_ui.py 通过 novel.py serve 启动, 不需要独立测
