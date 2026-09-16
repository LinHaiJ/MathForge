# 部署入口：读取平台注入的 PORT 环境变量（本地开发仍用 uvicorn app:app --port 8127）
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
