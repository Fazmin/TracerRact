# IP Asset Explorer

IP Asset Explorer merges BlueCat IPAM export with runZero scan results so you can search and filter every address by building, room, owner, device type, risk, open ports and more. It is a Flask app with a SQLite database and runs in Docker.

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
| `git clone https://github.com/Fazmin/TracerRact.git` | Clone the repo |
| `./start.sh` | Starts the app on the default 8080 |
| `./start.sh --port 9090` | Use another port (example using 9090) |
| `./start.sh --lan` | Let other computers on your network open it; prints your LAN address |
| `./start.sh --reset` | Delete all uploaded data and start again empty |
| `./start.sh --no-open` | Don't open a browser |
| `./start.sh stop` | Stop it. Your data is kept |

On Windows, run `.\start.cmd` in PowerShell or `start.cmd` in Command Prompt. It does the same as `./start.sh` and takes the same options, for example `.\start.cmd --port 9090` or `.\start.cmd stop`. It runs `start.ps1` with PowerShell, so you don't need to change the execution policy.

## Get the BlueCat export

`scripts/blucat_export.py` exports every IPv4 address from BlueCat Address Manager to a CSV that the explorer's BlueCat upload reads as is. It only reads from BlueCat, through the BAM REST v1 API. You need Python 3, the `requests` package and a BlueCat account with API access.

Run it from this folder and write the CSV somewhere outside it:

```sh
pip install requests
export BAM_URL=https://your-bluecat-server   # PowerShell: $env:BAM_URL = "https://your-bluecat-server"
export BAM_USER=your_api_username            # PowerShell: $env:BAM_USER = "your_api_username"
python scripts/blucat_export.py --out ../bluecat.csv
```

It asks for anything you didn't set, and for your password unless you set `BAM_PASS`. When it finishes, click **Load runZero / BlueCat file** and upload the CSV as the BlueCat file.

| Option | What it does |
|---|---|
| `--block 172.30.0.0/16` | Export one block only. Good for a first test run |
| `--no-hosts` | Skip the host name lookups. Much faster, but the Host Name column is empty |
| `--config NAME` | BlueCat configuration to read. Default: `<institution name>` |
| `--out PATH` | Where to write the CSV. Default: `bluecat_allocated_ips_<date>.csv` in the current folder |

Owner and Room come from BlueCat user-defined fields named `Owner` and `Room`. If yours are named differently, change `UDF_OWNER` and `UDF_ROOM` at the top of the script.

## IP Tracker

The **IP Tracker** section (next to **Explorer** at the top) checks addresses on a schedule to see whether they are still in use.

### Start a scan

Under **New scan**, enter a CIDR block (`130.113.10.0/24`), a range (`130.113.10.5-130.113.10.40`) or single IPs, separated by spaces or new lines. Blocks skip their network and broadcast addresses. A scan can have up to 10,000 addresses. Then choose:

- **Check**: every 4, 8 or 12 hours, every day, every 3 days, every month (30 days) or every 2 months (60 days).
- **Pace the checks**: check one address at a time, with a random pause of 0.5 to 4 seconds between addresses, instead of checking them all at once. Use it when you don't want a burst of traffic, for example on a network that an intrusion detection system watches. A paced /24 takes about 10 minutes per round if every address answers and about 40 if none do. The form shows an estimate, and warns you when a round would take longer than the interval. Paced checks from all scans share one queue.

You can also start a scan from the Explorer: filter to the assets you want, for example `cov:"BlueCat only"`, and click **Track these IPs**. To track a single address on its own, click **Track this IP** in an asset's detail panel.

The **Scans** table lists each scan with its counts: how many addresses answered, how many didn't, and how many are in each no-reply bucket. Click a number to list those addresses. From the table you can change a scan's interval or pacing, check it now, pause it, or remove it with its history. An address belongs to one scan at most. If a new scan includes addresses you track on their own, they join it and take its settings. Addresses that are already in another scan stay there. Removing a scan stops tracking all of its addresses, including ones that joined it.

**Tracked addresses** lists the addresses under their scans. Each scan has a row with its name, what it covers, how many addresses it has and how they are doing. Click the row to open the scan and list its addresses, 200 at a time. A search or a tile shows how many addresses match in each scan and hides the scans with no matches. When there are 200 matches or fewer, the scans open on their own. Click **One list** to list every address together, or choose a scan from the menu to list only its addresses.

### How an address is checked

A check pings the address with [fping](https://fping.org/). If there is no answer, it tries a TCP connection to ports 80, 443, 22, 445 and 3389. A connection or a refusal both count as alive, as with `nmap -sn`. Click an address to see its profile: uptime, last reply, a 180-day availability strip and recent checks.

### No-reply buckets

An address that doesn't answer is counted from its last reply, or from when tracking started if it has never answered. The **No reply for** tiles sort these addresses into buckets: 30+ days, 60+ days, 90+ days, 6+ months (180 days) and 12+ months (365 days). The buckets overlap: an address with no reply for 100 days is in the 30+, 60+ and 90+ day buckets. The tiles and the address list follow the scan and search you choose. **Download CSV** exports the list, including how many days each address has gone without a reply.

### Background checks

Checks run in the background for as long as the container runs. Tracked addresses and their history are kept in the same Docker volume as your uploads, so they survive restarts, and checks pick up where they left off.

| Environment variable | What it does |
|---|---|
| `TRACKER=off` | Don't run checks |
| `TRACKER_TCP_PORTS=80,443,22,445,3389` | Ports tried when an address doesn't answer ping. Leave empty to use ping only |

Things to know:
- **Checks come from your computer.** The pings and connection attempts go out from the computer running Docker. Make sure that is allowed on your network before you track large ranges.
- **Firewalls can make addresses look alive.** A firewall that answers with a refusal on behalf of addresses that aren't there makes them look alive. The tracker tests this at startup and once a day: it checks `192.0.2.1`, a reserved address that is never in use. If anything answers for it, the TCP check is turned off and the Tracker page shows a warning.

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
.venv/bin/flask --app ip_explorer tracker          # IP Tracker checks (gunicorn runs them itself in Docker)
.venv/bin/pytest
```

Without fping installed, the tracker uses only the TCP check.

Developing against real data is optional. `init-db --bluecat PATH --runzero PATH` loads exports from anywhere on disk as built-in data. The import dialog's **Go back to the built-in** button then returns to them.

The tests use small synthetic files in `tests/fixtures/`. `tests/test_parity.py` compares the Python version with the original single-file `IP Asset Explorer_uni_2026.html` on your real exports. It runs the original JavaScript in Node and skips unless you point it at the files:

```sh
IPX_BLUECAT_CSV=/path/to/bluecat.csv IPX_RUNZERO_CSV=/path/to/runzero.csv \
IPX_ORIGINAL_HTML="/path/to/IP Asset Explorer_uni_2026.html" .venv/bin/pytest tests/test_parity.py
```

Its intermediate output goes to a temporary folder that is deleted afterwards. The explorer now matches on every address of a runZero asset (`address`, `addresses_scope` and `addresses_extra`), which the original did not, so the test ignores those additions; its docstring lists where results still differ by design.
