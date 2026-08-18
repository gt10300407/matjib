import os
import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("PORT") or os.getenv("KFM_PORT", "8787"))
    host = os.getenv("KFM_HOST", "127.0.0.1")
    uvicorn.run("backend.app.main:app", host=host, port=port, reload=False)
