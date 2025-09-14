# Skoda Data Logger

A small, experimental project that subscribes to the MySkoda service and forwards relevant vehicle information to a local Graylog instance. Primary target vehicle is a Skoda Enyaq 80 (EV), but the approach may work with other models supported by MySkoda.

> Very much a vibe-code-thing: this was hacked together with a healthy dose of Copilot and curiosity. Expect rough edges.

## Features

- Event subscription to MySkoda for near‑real‑time status updates
- Graylog integration for centralized logs (charging status, battery, odometer, position, etc.)
- FastAPI endpoint to view the last 30 lines of the application log
- Early roadmap: persist charging sessions to a DB to calculate running costs

## Requirements

- Python 3.13
- Local or reachable Graylog server
- MySkoda account credentials
- Docker 

## Quick Start

### 1) Clone the repo
```bash
git clone https://github.com/tn8or/skoda.git
cd skoda
```

### 2) Create and activate a virtual environment
```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
```

### 3) Install dependencies
```bash
pip install -r requirements.txt
```

### 4) Configure environment
Create a `.env` file in the project root (or export these variables however you prefer):

```bash
# Required
SKODA_USER=<your-myskoda-username>
SKODA_PASS=<your-myskoda-password>
GRAYLOG_HOST=<your-graylog-host>
GRAYLOG_PORT=<your-graylog-port>
```

Make sure your Graylog server is running and reachable from where this app runs.

### 5) Run the app

- If the app uses Uvicorn (FastAPI), try:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

- Or simply:
```bash
python main.py
```

Then open:
```
GET http://localhost:8000/
```
This returns the last 30 lines of `app.log`.

## Docker

Build:
```bash
docker build -t skoda-data-logger .
```

Run:
```bash
docker run -d \
  -p 8000:8000 \
  -e SKODA_USER=<your-myskoda-username> \
  -e SKODA_PASS=<your-myskoda-password> \
  -e GRAYLOG_HOST=<your-graylog-host> \
  -e GRAYLOG_PORT=<your-graylog-port> \
  skoda-data-logger
```

Tip: If you want to persist the local log file, add a volume:
```bash
-v $(pwd)/app.log:/app/app.log
```

## Project Structure

```
.
├── main.py                 # Main application / FastAPI entrypoint
├── requirements.txt        # Python dependencies
├── .github/
│   └── workflows/
│       └── ghcr-image.yml  # CI to build/push Docker images (GHCR)
├── README.md               # This file
└── app.log                 # Runtime log file (created at runtime)
```

## Logging

- Graylog: Logs are sent via graypy to your Graylog server (configure host/port with env vars).
- File: Local logs are written to `app.log` to simplify debugging.

## API

- GET `/` — returns the last 30 lines of `app.log`.

Future improvement ideas:
- Optional query parameter like `?lines=100` to control the amount of log lines returned.
- Additional endpoints (healthz/metrics).

## CI/CD

The `ghcr-image.yml` workflow builds and pushes a Docker image to GitHub Container Registry (GHCR). A deployment webhook can be invoked after the image is pushed.

## Roadmap

- Persist charging session data to a database to compute running costs
- Enrich analytics (efficiency, costs, battery health trends)
- Harden error handling and reconnection logic

## Contributing

PRs and issues are welcome—especially improvements, bug fixes, and docs. This is a scrappy project; small quality-of-life fixes are appreciated.

## License

MIT — see [LICENSE](LICENSE).

## Author

Mostly GitHub Copilot, with a bit by Tommy Eriksen.

