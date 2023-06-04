#!/usr/bin/env python3
"""
Export every allocated IPv4 address from BlueCat Address Manager to CSV.

Usage:
    pip install requests
    $env:BAM_URL = "https://your-bluecat-server"   (PowerShell; Command Prompt: set BAM_URL=...,
    $env:BAM_USER = "your_api_username"            macOS/Linux: export BAM_URL=...)
    python blucat_export.py                (prompts for the password unless BAM_PASS is set)

Optional:
    python blucat_export.py --config McMaster --out my_export.csv --no-hosts

Uses the BAM REST v1 API (available in BAM 9.3.x). Read-only: it only calls
login, getEntityByName, getEntities, getLinkedEntities and logout.
NOTE: written against BlueCat's documented v1 API but not yet run against your
server, so test it on one block first (use --block 172.30.0.0/16).
"""
import argparse, csv, getpass, os, re, sys, time
import requests

API_PATH = "/Services/REST/v1"   # appended to the server from BAM_URL
STATE = {"STATIC": "Static", "DHCP_RESERVED": "DHCP Reserved", "GATEWAY": "Gateway",
         "RESERVED": "Reserved", "DHCP_FREE": "DHCP Free", "DHCP_ALLOCATED": "DHCP Allocated",
         "MAKE_STATIC": "Static", "MAKE_RESERVED": "Reserved", "MAKE_DHCP_RESERVED": "DHCP Reserved"}
# Names of your user-defined fields in BlueCat (as shown on the Addresses table).
UDF_OWNER, UDF_ROOM = "Owner", "Room"


def props(entity):
    """Turn BlueCat's 'k=v|k=v|' properties string into a dict."""
    out = {}
    for part in (entity.get("properties") or "").split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


class Bam:
    def __init__(self, server, user, pw):
        self.base = server.rstrip("/") + API_PATH
        self.s = requests.Session()
        r = self.s.get(f"{self.base}/login", params={"username": user, "password": pw}, timeout=60)
        r.raise_for_status()
        m = re.search(r"(BAMAuthToken:\s*\S+)", r.text)
        if not m:
            sys.exit("Login failed: " + r.text[:200])
        self.s.headers["Authorization"] = m.group(1)

    def get(self, path, **params):
        for attempt in range(3):
            r = self.s.get(f"{self.base}/{path}", params=params, timeout=120)
            if r.status_code == 200:
                return r.json()
            time.sleep(2 * (attempt + 1))
        r.raise_for_status()

    def entities(self, parent, etype, page=100):
        start = 0
        while True:
            batch = self.get("getEntities", parentId=parent, type=etype, start=start, count=page) or []
            yield from batch
            if len(batch) < page:
                return
            start += page

    def close(self):
        try:
            self.s.get(f"{self.base}/logout", timeout=30)
        except Exception:
            pass


def walk_networks(bam, parent_id, trail):
    """Yield (block_trail, network_entity) for every network under a block, any depth."""
    for blk in bam.entities(parent_id, "IP4Block"):
        yield from walk_networks(bam, blk["id"], trail + [props(blk).get("CIDR", blk["name"])])
    for net in bam.entities(parent_id, "IP4Network"):
        yield trail, net


def host_names(bam, addr_id):
    try:
        recs = bam.get("getLinkedEntities", entityId=addr_id, type="HostRecord", start=0, count=20) or []
    except Exception:
        return ""
    return ", ".join(props(r).get("absoluteName", r["name"]) for r in recs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="McMaster", help="BlueCat configuration name")
    ap.add_argument("--out", default=f"bluecat_allocated_ips_{time.strftime('%Y-%m-%d')}.csv")
    ap.add_argument("--block", help="only export this block, e.g. 172.30.0.0/16 (for testing)")
    ap.add_argument("--no-hosts", action="store_true", help="skip host name lookups (much faster)")
    a = ap.parse_args()

    server = os.environ.get("BAM_URL") or input("BlueCat server, e.g. https://bam.example.com: ")
    if "://" not in server:
        server = "https://" + server
    user = os.environ.get("BAM_USER") or input("BlueCat username: ")
    pw = os.environ.get("BAM_PASS") or getpass.getpass("BlueCat password: ")
    bam = Bam(server, user, pw)
    try:
        cfg = bam.get("getEntityByName", parentId=0, name=a.config, type="Configuration")
        if not cfg or not cfg.get("id"):
            sys.exit(f"Configuration '{a.config}' not found")
        header = ["Parent Block", "Subnet", "Subnet Name", "IP Address", "Host Name", "Address Name",
                  "MAC Address", "Location", "Owner", "Room", "State"]
        n = 0
        with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(header)
            for trail, net in walk_networks(bam, cfg["id"], []):
                np = props(net)
                cidr = np.get("CIDR", net["name"])
                parent = " > ".join(trail)
                if a.block and a.block not in trail:
                    continue
                count = 0
                for ad in bam.entities(net["id"], "IP4Address"):
                    p = props(ad)
                    state = STATE.get(p.get("state", ""), p.get("state", ""))
                    host = "" if a.no_hosts or state in ("Gateway", "DHCP Free") else host_names(bam, ad["id"])
                    w.writerow([parent, cidr, net["name"], p.get("address", ""), host, ad.get("name") or "",
                                p.get("macAddress", ""), p.get("locationCode", ""),
                                p.get(UDF_OWNER, ""), p.get(UDF_ROOM, ""), state])
                    count += 1
                n += count
                print(f"{cidr:<20} {count:>5}  {net['name'][:50]}")
        print(f"\nWrote {n} addresses to {a.out}")
    finally:
        bam.close()


if __name__ == "__main__":
    main()