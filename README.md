# Employee Attendance & Analytics API

FastAPI + MongoDB implementation for the HROne attendance assignment.

The app starts from the required entrypoint:

```bash
uvicorn app.main:app --port 8000
```

All application code is in:

```text
app/main.py
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Set MongoDB environment variables:

```bash
set MONGO_URI=mongodb://localhost:27017
set MONGO_DB=attendance_db
```

PowerShell equivalent:

```powershell
$env:MONGO_URI="mongodb://localhost:27017"
$env:MONGO_DB="attendance_db"
```

MongoDB 6.0+ is required.

## Run

```bash
uvicorn app.main:app --port 8000
```

Health check:

```bash
curl http://localhost:8000/health
```

## Test

```bash
python -m py_compile app\main.py
python -c "from app.main import app; print(len(app.openapi()['paths']))"
```

## Implemented

- Employee create/list with unique `emp_code`.
- Punch-in with IST attendance date, overnight shift handling, late minutes, and duplicate protection.
- Punch-out with work hours, overtime, half-day, and race-safe update.
- Manual correction with derived-field recomputation and audit history.
- Attendance listing with filters and pagination.
- Monthly employee analytics.
- Department monthly summary.
- Late leaderboard with tie-aware ranking.
- Department trend with gap-filled daily rows.
- Admin explain endpoint using the same query builders as the real endpoints.

## Notes

- No `.env`, secrets, or Dockerfile are included.
- Request/response instants use epoch milliseconds; MongoDB stores BSON datetimes.
- Analytics read stored derived values instead of recomputing from punch times.
