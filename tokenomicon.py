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
import socket
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "tokenomicon.html"
PLACEHOLDER = "/*__TOKENOMICON_DATA__*/null"


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
    text = " ".join(text.split())
    if not text or text.startswith("<") or text.startswith("Caveat:"):
        return None
    return text[:200]


def slim(obj):
    """Keep only what the dashboard reads. Your code and Claude's replies stay on disk."""
    t = obj.get("type")
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
            "cache_creation_input_tokens", "cache_creation", "speed") if k in usage}
        out["message"] = {"id": msg.get("id"), "model": msg.get("model"), "usage": keep}
        if obj.get("requestId"):
            out["requestId"] = obj["requestId"]
    else:
        p = None if obj.get("isSidechain") else prompt_text(msg.get("content"))
        out["message"] = {"role": "user", "content": p} if p else {}
    return out


def collect(dirs):
    records, hints, files, bad = [], [], 0, 0
    for root in dirs:
        for path in sorted(root.rglob("*.jsonl")):
            files += 1
            rel = path.relative_to(root)
            hint = rel.parts[0].lstrip("-") if len(rel.parts) > 1 else None
            try:
                with path.open("r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = slim(json.loads(line))
                        except (json.JSONDecodeError, AttributeError, TypeError):
                            bad += 1
                            continue
                        if rec:
                            records.append(rec)
                            hints.append(hint)
            except OSError as e:
                print(f"  skipping {path}: {e}", file=sys.stderr)
    return records, hints, files, bad


def build_report(dirs, quiet=False):
    """Scan the transcripts and return the dashboard HTML with the data baked in."""
    t0 = time.time()
    records, hints, files, bad = collect(dirs)
    if not quiet:
        sessions = len({r["sessionId"] for r in records})
        print(f"Read {files} files, {len(records):,} records, {sessions:,} sessions in {time.time() - t0:.1f}s"
              + (f" ({bad} junk lines skipped)" if bad else ""))
    payload = {
        "generatedAt": int(time.time() * 1000),
        "files": files,
        "records": records,
        "hints": hints,
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


def serve(dirs, host, port, open_browser):
    """Live mode: every page load rescans your logs, so a refresh is always current."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?")[0] not in ("/", "/index.html"):
                self.send_error(404)
                return
            body = build_report(dirs, quiet=True).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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

    if args.serve:
        build_report(dirs)  # fail fast and show counts before going live
        serve(dirs, args.host, args.port, not args.no_open)
        return

    out = Path(args.out).expanduser()
    out.write_text(build_report(dirs), encoding="utf-8")
    print(f"Report: {out}")
    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
