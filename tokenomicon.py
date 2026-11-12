#!/usr/bin/env python3
"""Tokenomicon collector.

Scans your Claude Code transcripts (~/.claude/projects/**/*.jsonl), keeps only
the bits the dashboard needs (timestamps, models, token usage, first prompts),
bakes them into tokenomicon-report.html and opens it in your browser.

Nothing is uploaded anywhere. No dependencies beyond Python 3.8+.

    python3 tokenomicon.py                 # scan, build, open
    python3 tokenomicon.py --no-open       # just build the report
    python3 tokenomicon.py --serve         # live dashboard at http://localhost:8787
    python3 tokenomicon.py --dir /some/other/projects --out ~/Desktop/spend.html
"""
import argparse
import json
import os
import re
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "tokenomicon.html"
PLACEHOLDER = "/*__TOKENOMICON_DATA__*/null"
CLOUD_GLOB = "tokenomicon-cloud*.json"

# Slash-command and reminder blocks are noise. Any other wrapper tag (Remote Control, the desktop app)
# just hides the text you typed, so peel it off instead of throwing the whole prompt away.
NOISE_TAGS = re.compile(r"<(system-reminder|command-name|command-message|command-args|local-command-stdout|"
                        r"local-command-stderr|local-command-caveat|bash-input|bash-stdout|bash-stderr)\b[^>]*>.*?</\1>", re.S)
ANY_TAG = re.compile(r"</?[A-Za-z][\w:.-]*(\s[^>]*)?/?>")


def default_dirs():
    dirs = []
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        for part in env.split(","):
            if part.strip():
                dirs.append(Path(part.strip()).expanduser() / "projects")
    home = Path.home()
    dirs += [home / ".claude" / "projects", home / ".config" / "claude" / "projects"]
    seen, out = set(), []
    for d in dirs:
        r = d.resolve() if d.exists() else d
        if r not in seen and d.is_dir():
            seen.add(r)
            out.append(d)
    return out


def prompt_text(content):
    """First real human prompt, trimmed. Tool results and slash-command noise are skipped."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        text = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    else:
        return None
    if text.lstrip().startswith("<"):
        text = ANY_TAG.sub(" ", NOISE_TAGS.sub(" ", text))
    text = " ".join(text.split())
    if not text or text.startswith("Caveat:") or text.startswith("[Request interrupted"):
        return None
    return text[:200]


def slim(obj):
    """Keep only what the dashboard reads. Your code and Claude's replies stay on disk."""
    t = obj.get("type")
    if t == "cost-state" and obj.get("sessionId"):
        # Claude Code's own running tally (newer versions only). Totals only, no content.
        return {k: obj.get(k) for k in ("type", "sessionId", "totalCostUSD", "totalAPIDuration",
                                         "totalToolDuration", "totalDuration", "startTime")}
    # Session names, exactly as Claude Code shows them in /resume:
    # your /rename title, else the auto-generated title, else an older-style summary.
    if t == "custom-title" and obj.get("sessionId") and obj.get("customTitle"):
        return {"type": t, "sessionId": obj["sessionId"], "customTitle": obj["customTitle"]}
    if t == "ai-title" and obj.get("sessionId") and obj.get("aiTitle"):
        return {"type": t, "sessionId": obj["sessionId"], "aiTitle": obj["aiTitle"]}
    if t == "summary" and obj.get("leafUuid") and obj.get("summary"):
        # points at a message uuid, not a session; collect() resolves it
        return {"type": t, "leafUuid": obj["leafUuid"], "summary": obj["summary"]}
    if t not in ("user", "assistant") or not obj.get("sessionId") or not obj.get("timestamp"):
        return None
    out = {
        "type": t,
        "sessionId": obj["sessionId"],
        "timestamp": obj["timestamp"],
    }
    if obj.get("cwd"):
        out["cwd"] = obj["cwd"]
    if obj.get("isSidechain"):
        out["isSidechain"] = True
    msg = obj.get("message") or {}
    if t == "assistant":
        usage = msg.get("usage")
        if not usage:
            out["message"] = {}
            return out  # still useful as a timestamp
        keep = {k: usage[k] for k in (
            "input_tokens", "output_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens", "cache_creation", "speed",
            "output_tokens_details", "server_tool_use") if k in usage}
        out["message"] = {"id": msg.get("id"), "model": msg.get("model"), "usage": keep}
        if obj.get("requestId"):
            out["requestId"] = obj["requestId"]
    else:
        # isMeta lines are injected context (skills, reminders), not something you typed
        p = None if obj.get("isSidechain") or obj.get("isMeta") else prompt_text(msg.get("content"))
        out["message"] = {"role": "user", "content": p} if p else {}
    return out


class Collector:
    """Remembers every transcript it has read, keyed by path, so a rescan only re-reads files that changed.
    That's what makes Live Track cheap: polling every few seconds re-parses one or two growing files, not 35 MB."""

    def __init__(self, dirs):
        self.dirs = dirs
        self.files = {}  # path -> {"sig", "recs", "hints", "uuids", "summaries", "sids", "bad"}
        self.lock = threading.Lock()

    @staticmethod
    def _parse(path, hint, sig):
        recs, uuids, summaries, sids, bad = [], {}, [], set(), 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        rec = slim(obj)
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        bad += 1  # includes a half-written last line while Claude is mid-reply
                        continue
                    if obj.get("uuid") and obj.get("sessionId"):
                        uuids[obj["uuid"]] = obj["sessionId"]
                    if not rec:
                        continue
                    if rec["type"] == "summary":
                        summaries.append(rec)
                        continue
                    recs.append(rec)
                    sids.add(rec["sessionId"])
        except OSError as e:
            print(f"  skipping {path}: {e}", file=sys.stderr)
        return {"sig": sig, "recs": recs, "hint": hint, "uuids": uuids, "summaries": summaries, "sids": sids, "bad": bad}

    def refresh(self, now=None):
        """Re-read new or changed files, stamping them with `now` (ms). Returns the session ids whose data changed."""
        changed, seen = set(), set()
        now = now if now is not None else int(time.time() * 1000)
        for root in self.dirs:
            for path in sorted(root.rglob("*.jsonl")):
                try:
                    st = path.stat()
                except OSError:
                    continue
                seen.add(path)
                sig = (st.st_mtime_ns, st.st_size)
                old = self.files.get(path)
                if old and old["sig"] == sig:
                    continue
                rel = path.relative_to(root)
                hint = rel.parts[0].lstrip("-") if len(rel.parts) > 1 else None
                new = self._parse(path, hint, sig)
                new["at"] = now
                self.files[path] = new
                changed |= new["sids"] | (old["sids"] if old else set())
        for path in [p for p in self.files if p not in seen]:
            changed |= self.files.pop(path)["sids"]
        return changed

    def changed_since(self, since):
        """Session ids from files re-read at or after `since` (ms). Each open page tracks its own `since`."""
        out = set()
        for f in self.files.values():
            if f["at"] >= since:
                out |= f["sids"]
        return out

    def assemble(self, only=None):
        """Records (and project hints) for every session, or just the ids in `only`."""
        records, hints, bad = [], [], 0
        uuid_session = {}
        for f in self.files.values():
            uuid_session.update(f["uuids"])
            bad += f["bad"]
            if only is not None and not (f["sids"] & only):
                continue
            for rec in f["recs"]:
                if only is None or rec["sessionId"] in only:
                    records.append(rec)
                    hints.append(f["hint"])
        for f in self.files.values():
            for rec in f["summaries"]:
                sid = uuid_session.get(rec["leafUuid"])
                if sid and (only is None or sid in only):
                    records.append({"type": "summary", "sessionId": sid, "summary": rec["summary"]})
                    hints.append(None)
        return records, hints, len(self.files), bad


def load_cloud(paths, quiet=False):
    """Cloud sessions (desktop app, web, phone, project threads) never write transcripts to your disk.
    Claude keeps running totals for them instead; a tokenomicon-cloud.json export carries those."""
    out, seen = [], set()
    for path in paths:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"  skipping cloud export {path}: {e}", file=sys.stderr)
            continue
        if not isinstance(data, dict) or data.get("tokenomicon") != "cloud-sessions":
            print(f"  skipping {path}: not a Tokenomicon cloud export", file=sys.stderr)
            continue
        for s in data.get("sessions") or []:
            if isinstance(s, dict) and s.get("id") and s["id"] not in seen:
                seen.add(s["id"])
                out.append(s)
    if out and not quiet:
        print(f"Plus {len(out)} cloud sessions from {len(paths)} export file(s)")
    return out


def build_report(collector, cloud_paths=(), quiet=False, live=False):
    """Scan the transcripts and return the dashboard HTML with the data baked in."""
    t0 = time.time()
    with collector.lock:
        generated = int(time.time() * 1000)
        collector.refresh(generated)
        records, hints, files, bad = collector.assemble()
    if not quiet:
        sessions = len({r["sessionId"] for r in records})
        print(f"Read {files} files, {len(records):,} records, {sessions:,} sessions in {time.time() - t0:.1f}s"
              + (f" ({bad} junk lines skipped)" if bad else ""))
    payload = {
        "generatedAt": generated + 1,  # also where Live Track starts counting from
        "files": files,
        "records": records,
        "hints": hints,
        "cloud": load_cloud(cloud_paths, quiet),
        "live": live,  # only the --serve page can poll for updates
    }
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in html:
        sys.exit("tokenomicon.html is missing its data placeholder. Grab a fresh copy.")
    return html.replace(PLACEHOLDER, blob, 1)


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; this just picks the outbound interface
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def serve(collector, cloud_paths, host, port, open_browser):
    """Live mode: every page load rescans your logs, so a refresh is always current."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = self.path.split("?")[0]
            if route == "/live":
                # Live Track polls this: only the sessions whose transcripts changed since the last look.
                try:
                    since = int(parse_qs(urlsplit(self.path).query).get("since", ["0"])[0])
                except ValueError:
                    since = 0
                with collector.lock:
                    now = int(time.time() * 1000)
                    collector.refresh(now)
                    sids = collector.changed_since(since)
                    records, hints, _, _ = collector.assemble(sids) if sids else ([], [], 0, 0)
                payload = {"now": now + 1, "sessions": sorted(sids), "records": records, "hints": hints}
                self._send(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
                           "application/json")
                return
            if route not in ("/", "/index.html"):
                self.send_error(404)
                return
            self._send(build_report(collector, cloud_paths(), quiet=True, live=True).encode("utf-8"),
                       "text/html; charset=utf-8")

        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    httpd = ThreadingHTTPServer((host, port), Handler)
    local = f"http://localhost:{port}/"
    print(f"Tokenomicon is live at {local}")
    if host in ("0.0.0.0", "::"):
        ip = lan_ip()
        if ip:
            print(f"On your network: http://{ip}:{port}/  (anyone on this Wi-Fi can see it)")
    print("Refresh the page for fresh numbers. Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(local)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down. Your wallet thanks you.")


def main():
    ap = argparse.ArgumentParser(description="Build a Tokenomicon report from your Claude Code transcripts.")
    ap.add_argument("--dir", action="append", help="projects folder to scan (repeatable). Default: ~/.claude/projects")
    ap.add_argument("--out", default=str(HERE / "tokenomicon-report.html"), help="where to write the report")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    ap.add_argument("--serve", action="store_true", help="run a live local server instead of writing a file")
    ap.add_argument("--cloud", action="append", metavar="FILE",
                    help="cloud session export to include (repeatable). Default: any tokenomicon-cloud*.json next to this script")
    ap.add_argument("--port", type=int, default=8787, help="port for --serve (default 8787)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="address for --serve (default 127.0.0.1; use 0.0.0.0 to share on your network)")
    args = ap.parse_args()

    if not TEMPLATE.exists():
        sys.exit(f"Can't find {TEMPLATE.name} next to this script. Keep tokenomicon.py and tokenomicon.html in the same folder.")

    dirs = [Path(d).expanduser() for d in args.dir] if args.dir else default_dirs()
    dirs = [d for d in dirs if d.is_dir()]
    if not dirs:
        sys.exit("No Claude Code transcripts found (looked in ~/.claude/projects). Pass --dir if yours live somewhere weird.")

    print("Summoning tokens from:")
    for d in dirs:
        print("  " + str(d))

    def cloud_paths():  # re-checked on every refresh in --serve mode, so a new export shows up without a restart
        if args.cloud:
            return [Path(c).expanduser() for c in args.cloud]
        return sorted(HERE.glob(CLOUD_GLOB))

    if args.serve:
        collector = Collector(dirs)
        build_report(collector, cloud_paths())  # fail fast and show counts before going live
        serve(collector, cloud_paths, args.host, args.port, not args.no_open)
        return

    out = Path(args.out).expanduser()
    out.write_text(build_report(Collector(dirs), cloud_paths()), encoding="utf-8")
    print(f"Report: {out}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
