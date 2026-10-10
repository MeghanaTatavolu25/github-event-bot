# github-event-bot

FastAPI app that will react to GitHub events. Step 1: health checks only.

## Run locally (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # then edit .env and put your real MONGODB_URI
uvicorn app.main:app --reload
```

If activation is blocked, run once: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Test

```powershell
curl.exe http://127.0.0.1:8000/health      # {"status":"ok"}
curl.exe http://127.0.0.1:8000/health/db   # {"status":"ok"} if MongoDB answers, else 503
```

Interactive docs: http://127.0.0.1:8000/docs
