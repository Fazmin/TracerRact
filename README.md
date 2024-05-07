# IP Asset Explorer

IP Asset Explorer merges McMaster's BlueCat IPAM export with runZero scan results so you can search and filter every address by building, room, owner, device type, risk, open ports and more. It is a Flask app with a SQLite database and runs in Docker.

## Install and run

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/) (macOS or Windows) or Docker Engine with the Compose plugin (Linux). Then run:

```sh
./start.sh
```

The script:
1. starts Docker Desktop if it isn't running
2. builds the image (about a minute the first time)
3. prints the address and opens it in your browser: <http://localhost:8080>

The first time, the explorer is empty. Click **Load runZero / BlueCat file** to upload your exports.

| Command | What it does |
|---|---|
| `./start.sh --port 9090` | Use another port |
| `./start.sh --lan` | Let other computers on your network open it; prints your LAN address |
| `./start.sh --reset` | Delete all uploaded data and start again empty |
| `./start.sh --no-open` | Don't open a browser |
| `./start.sh stop` | Stop it. Your data is kept |

On Windows without bash, run `docker compose up --build` instead. The log shows the address when the app is ready.

## Your data

- **Uploads only.** Exports are never part of the app. Upload a runZero asset export, a BlueCat export, or both. An upload replaces the previous file of the same kind.
- **Where it's stored.** Uploaded data lives in the `ip-asset-explorer_explorer-data` Docker volume. It survives restarts and rebuilds until you run `./start.sh --reset`.
- **Kept out of the folder.** Keep the export files somewhere other than this folder. `.gitignore` and `.dockerignore` exclude CSV files anyway.
- **Who can see it.** By default only your own computer can open the app. It has no login, so think before you use `--lan`.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/flask --app ip_explorer init-db          # empty database in instance/explorer.sqlite
.venv/bin/flask --app ip_explorer run --debug      # http://127.0.0.1:5000
.venv/bin/pytest
```

Developing against real data is optional. `init-db --bluecat PATH --runzero PATH` loads exports from anywhere on disk as built-in data. The import dialog's **Go back to the built-in** button then returns to them.

The tests use small synthetic files in `tests/fixtures/`. `tests/test_parity.py` compares the Python version with the original single-file `IP Asset Explorer_McMaster_2026.html` on your real exports. It runs the original JavaScript in Node and skips unless you point it at the files:

```sh
IPX_BLUECAT_CSV=/path/to/bluecat.csv IPX_RUNZERO_CSV=/path/to/runzero.csv \
IPX_ORIGINAL_HTML="/path/to/IP Asset Explorer_McMaster_2026.html" .venv/bin/pytest tests/test_parity.py
```

Its intermediate output goes to a temporary folder that is deleted afterwards.
