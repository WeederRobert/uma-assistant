# 开发模式：只启动本地服务，用浏览器访问（带热重载）
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8737 --reload
