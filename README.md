# Employee Attendance & Analytics API

FastAPI + MongoDB implementation for the HROne attendance assignment.

The app starts from the required entrypoint:

```bash
uvicorn app.main:app --port 8000
```

The code is split into small modules for readability:

```text
app/main.py              FastAPI app setup and router registration
app/database.py          Mongo client and indexes
app/constants.py         Shared constants and validation regex
app/schemas.py           Pydantic request models
app/utils.py             Business rules, serializers, aggregation builders
app/routes/employees.py  Employee endpoints
app/routes/attendance.py Punch-in, punch-out, regularization endpoints
app/routes/analytics.py  Analytics endpoints
app/routes/admin.py      Explain endpoint
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

## Seed Sample Data

```bash
python sample_seed.py
```

This loads the sample employees and attendance logs from `sample_data/`.

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
python -m unittest discover -v
python -m py_compile app\main.py app\constants.py app\database.py app\schemas.py app\utils.py app\routes\employees.py app\routes\attendance.py app\routes\analytics.py app\routes\admin.py
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
