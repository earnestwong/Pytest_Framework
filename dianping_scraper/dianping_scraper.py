#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dianping_scraper - Dianping (WeChat mini-program) review scraper CLI.

Designed to be called by both humans and automation agents.

Subcommands:
  check-env   Verify runtime environment (python, mitmproxy, CA cert, pywin32)
  capture     Start proxy, wait for review-list page, auto-scroll and capture traffic
  export      Parse captured .jsonl into store_reviews-ready CSV / JSON

Agent contract:
  - Pass --json to get a single-line JSON result on stdout (last line, key "status").
  - Exit codes: 0=success, 1=general error, 2=environment not ready,
                3=timeout waiting for review-list request, 4=stopped before target.
  - All human-readable logs go to stderr when --json is used (stdout stays clean).

Examples:
  dianping_scraper.exe check-env --json
  dianping_scraper.exe capture "FengYu (Huaihai)" 080501 --target 1400 --json
  dianping_scraper.exe export 080501 --org-code 080501 --store-name "FengYu" --format csv --json
"""
import argparse
import csv
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta

VERSION = "2.0.0"

if os.name == "nt":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_ENV = 2
EXIT_TIMEOUT = 3
EXIT_PARTIAL = 4

WHEEL_DELTA = 120
IS_WINDOWS = os.name == "nt"
REVIEW_API_KEYWORD = "reviewlist"   # matches outsidesiftedreviewlist / outsideshopreviewlist / ...
REPLY_API_KEYWORD = "paginateDpFeedReply"
MERCHANT_USER_TYPE = 10
DP_HOSTS = ("dianping.com", "dpfile.com", "meituan.com")

# ---------------------------------------------------------------------------
# logging / result helpers
# ---------------------------------------------------------------------------
_QUIET = False
_JSON_MODE = False
_LOG_FP = None


def log(msg, level="INFO"):
    """Human-readable log -> stderr (and optional log file)."""
    if _QUIET:
        return
    line = f"[{time.strftime('%H:%M:%S')}] [{level}] {msg}"
    print(line, file=sys.stderr, flush=True)
    if _LOG_FP:
        try:
            _LOG_FP.write(line + "\n")
            _LOG_FP.flush()
        except Exception:
            pass


def emit_result(payload):
    """Machine-readable result -> stdout as single-line JSON."""
    payload = dict(payload)
    payload.setdefault("tool", "dianping_scraper")
    payload.setdefault("version", VERSION)
    payload["finished_at"] = datetime.now().isoformat(timespec="seconds")
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def open_log_file(path):
    global _LOG_FP
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        _LOG_FP = open(path, "a", encoding="utf-8")
    except Exception as ex:
        log(f"cannot open log file {path}: {ex}", "WARN")


def close_log_file():
    global _LOG_FP
    if _LOG_FP:
        try:
            _LOG_FP.close()
        except Exception:
            pass
        _LOG_FP = None

# ---------------------------------------------------------------------------
# environment helpers
# ---------------------------------------------------------------------------

def candidate_pythons():
    """All discoverable Python 3.11+ interpreters (absolute paths, deduped)."""
    candidates = []
    if sys.executable and "python" in os.path.basename(sys.executable).lower():
        candidates.append(sys.executable)
    lad = os.environ.get("LOCALAPPDATA", "")
    for v in ("Python313", "Python312", "Python311"):
        candidates.append(os.path.join(lad, "Programs", "Python", v, "python.exe"))
    candidates += ["python", "python3", "py"]
    found = []
    seen = set()
    for c in candidates:
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            out = subprocess.run([c, "--version"], capture_output=True, text=True,
                                 timeout=15)
            if out.returncode != 0:
                continue
            m = re.search(r"Python 3\.(\d+)", (out.stdout or "") + (out.stderr or ""))
            if not m or int(m.group(1)) < 11:
                continue
            exe = c if os.path.isfile(c) else shutil_which(c)
            if exe:
                exe = os.path.abspath(exe)
                k2 = exe.lower()
                if k2 not in seen:
                    seen.add(k2)
                    found.append(exe)
        except Exception:
            continue
    return found


def find_python(required_modules=()):
    """First Python 3.11+ that can actually import required_modules.

    Never trusts pip metadata: a binary package like pywin32 bridged from a
    venv via .pth reports 'installed' but fails to import at runtime."""
    for exe in candidate_pythons():
        if all(py_has_module(exe, m) for m in required_modules):
            return exe
    return None


def shutil_which(cmd):
    try:
        import shutil
        return shutil.which(cmd)
    except Exception:
        return None


def py_has_module(python_exe, module):
    try:
        r = subprocess.run([python_exe, "-c", f"import {module}"],
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def find_mitmdump(python_exe):
    """Return (mode, path): mode='exe' -> [path], mode='module' -> [python,'-c',...]."""
    if python_exe:
        scripts = os.path.join(os.path.dirname(python_exe), "Scripts")
        exe = os.path.join(scripts, "mitmdump.exe")
        if os.path.isfile(exe):
            return ("exe", exe)
        if py_has_module(python_exe, "mitmproxy"):
            return ("module", python_exe)
    return (None, None)


def mitmproxy_cert_path():
    return os.path.join(os.path.expanduser("~"), ".mitmproxy", "mitmproxy-ca-cert.cer")


def cert_is_trusted():
    """Check mitmproxy CA in current-user OR local-machine Root store."""
    for extra in (["-user"], []):
        try:
            r = subprocess.run(["certutil", "-store"] + extra + ["Root", "mitmproxy"],
                               capture_output=True, timeout=30)
            out = (r.stdout or b"").decode("gbk", errors="ignore")
            if "mitmproxy" in out:
                return True
        except Exception:
            continue
    return False


def cmd_check_env(args):
    cands = candidate_pythons()
    python_exe = cands[0] if cands else None
    mitm_mode, mitm_path = (None, None)
    if python_exe:
        mitm_mode, mitm_path = find_mitmdump(python_exe)
    # win32gui is imported in-process (bundled inside the exe; must exist in the
    # running interpreter for the .py version). The external python found above
    # is only used to launch mitmdump and does NOT need pywin32.
    if IS_WINDOWS:
        try:
            import win32gui  # noqa: F401
            pywin32_ok = True
        except Exception:
            pywin32_ok = False
    else:
        pywin32_ok = True
    cert_file = os.path.isfile(mitmproxy_cert_path())
    cert_trusted = cert_is_trusted() if IS_WINDOWS else cert_file

    checks = {
        "python": {"ok": bool(python_exe), "path": python_exe},
        "mitmproxy": {"ok": bool(mitm_mode), "mode": mitm_mode, "path": mitm_path},
        "pywin32": {"ok": pywin32_ok, "scope": "in-process"},
        "ca_cert_file": {"ok": cert_file, "path": mitmproxy_cert_path()},
        "ca_cert_trusted": {"ok": cert_trusted},
    }
    ready = all(c["ok"] for c in checks.values())
    result = {"status": "ok" if ready else "env_not_ready", "ready": ready, "checks": checks}

    if _JSON_MODE:
        emit_result(result)
    else:
        log(f"python      : {python_exe or 'NOT FOUND'}")
        log(f"mitmproxy   : {mitm_mode or 'NOT FOUND'} {mitm_path or ''}")
        log(f"pywin32     : {'ok' if pywin32_ok else 'MISSING'}")
        log(f"ca cert file: {'ok' if cert_file else 'MISSING'} ({mitmproxy_cert_path()})")
        log(f"ca trusted  : {'ok' if cert_trusted else 'NOT INSTALLED'}")
        log(f"ready       : {ready}")
        if not _QUIET:
            print(json.dumps(result, ensure_ascii=False, indent=2))

    return EXIT_OK if ready else EXIT_ENV

# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------
ADDON_TEMPLATE = r'''
import json, os
from mitmproxy import http, ctx

OUTPUT_FILE = os.environ.get("DP_CAPTURE_FILE", "dianping_capture.jsonl")
HOSTS = ("dianping.com", "dpfile.com", "meituan.com")

def _is_dp(flow):
    host = flow.request.pretty_host or ""
    return any(h in host for h in HOSTS)

def request(flow: http.HTTPFlow):
    if not _is_dp(flow):
        return
    req = flow.request
    entry = {"type": "request", "method": req.method, "url": req.pretty_url,
             "headers": dict(req.headers), "body": req.get_text(strict=False),
             "timestamp": req.timestamp_start}
    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def response(flow: http.HTTPFlow):
    if not _is_dp(flow):
        return
    resp = flow.response
    ct = (resp.headers.get("content-type") or "").lower()
    if any(x in ct for x in ("image/", "font/", "video/", "audio/", "octet-stream")):
        body = ""
    else:
        body = resp.get_text(strict=False)
    entry = {"type": "response", "url": flow.request.pretty_url,
             "status": resp.status_code, "headers": dict(resp.headers),
             "body": body, "timestamp": resp.timestamp_end}
    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    ctx.log.info(f"[DP] {resp.status_code} {flow.request.pretty_url[:120]}")
'''


def write_addon():
    fd, path = tempfile.mkstemp(suffix=".py", prefix="dp_addon_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(ADDON_TEMPLATE)
    return path


def set_system_proxy(enable, port):
    import winreg
    import ctypes
    key = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_ALL_ACCESS) as k:
        winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
        if enable:
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, f"127.0.0.1:{port}")
    ctypes.windll.wininet.InternetSetOptionW(0, 39, 0, 0)
    ctypes.windll.wininet.InternetSetOptionW(0, 37, 0, 0)


def kill_port(port):
    if not IS_WINDOWS:
        return
    ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    subprocess.run([ps, "-Command",
        f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty OwningProcess -Unique | "
        "ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"],
        capture_output=True)


def wait_port(host, port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def find_target_window(keywords):
    if not IS_WINDOWS:
        return None
    import win32gui
    result = []
    kws = [k.lower() for k in keywords]

    def cb(hwnd, _):
        try:
            title = win32gui.GetWindowText(hwnd)
            if win32gui.IsWindowVisible(hwnd) and title:
                tl = title.lower()
                if any(k in tl or k in title for k in kws):
                    result.append((hwnd, title, win32gui.GetWindowRect(hwnd)))
        except Exception:
            pass

    win32gui.EnumWindows(cb, None)
    return result[0] if result else None


def scroll_down(clicks):
    import ctypes
    for _ in range(clicks):
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, -WHEEL_DELTA, 0)
        time.sleep(0.05)


def count_reviews(capture_file, prog):
    """Incrementally parse capture file; returns (unique_review_count, max_start).

    prog tracks: offset, mids (set of mainId), max_start, is_end (from API isEnd),
    shop_review_count (total count reported by API)."""
    try:
        with open(capture_file, "rb") as f:
            f.seek(prog["offset"])
            chunk = f.read()
            nl = chunk.rfind(b"\n")
            if nl == -1:
                return len(prog["mids"]), prog["max_start"]
            prog["offset"] += nl + 1
            text = chunk[:nl + 1].decode("utf-8", errors="ignore")
    except FileNotFoundError:
        return len(prog["mids"]), prog["max_start"]
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if not isinstance(e, dict) or e.get("type") != "response":
            continue
        if REVIEW_API_KEYWORD not in e.get("url", ""):
            continue
        body = e.get("body", "")
        if not body:
            continue
        try:
            data = json.loads(body)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        r = data.get("result")
        r = r if isinstance(r, dict) else {}
        for item in (data.get("list") or r.get("reviewList") or []):
            if not isinstance(item, dict):
                continue
            mid = str(item.get("mainId", ""))
            if mid:
                prog["mids"].add(mid)
        si = data.get("startIndex")
        if si is None:
            si = r.get("startIndex", 0)
        if isinstance(si, int) and si > prog["max_start"]:
            prog["max_start"] = si
        if data.get("isEnd") is True or r.get("isEnd") is True:
            prog["is_end"] = True
        src = data.get("shopReviewCount")
        if src is None:
            src = r.get("shopReviewCount")
        if isinstance(src, int) and src > (prog["shop_review_count"] or 0):
            prog["shop_review_count"] = src
    return len(prog["mids"]), prog["max_start"]


def wait_for_first_request(capture_file, baseline_size, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            cur = os.path.getsize(capture_file)
        except FileNotFoundError:
            cur = 0
        if cur > baseline_size + 100:
            with open(capture_file, "r", encoding="utf-8", errors="ignore") as f:
                f.seek(baseline_size)
                for line in f:
                    if REVIEW_API_KEYWORD in line:
                        return True
        time.sleep(2)
    return False


def cmd_capture(args):
    if not IS_WINDOWS:
        emit_result({"status": "error", "error": "capture requires Windows"})
        return EXIT_ERROR

    store_name = args.store_name
    store_id = args.store_id
    out_dir = os.path.abspath(args.output_dir or os.getcwd())
    os.makedirs(out_dir, exist_ok=True)
    capture_file = os.path.abspath(args.capture_file) if args.capture_file \
        else os.path.join(out_dir, f"{store_id}_capture.jsonl")
    open_log_file(args.log_file or os.path.join(out_dir, f"{store_id}_capture.log"))

    result = {
        "status": "error",
        "store_name": store_name,
        "store_id": store_id,
        "capture_file": capture_file,
        "target": args.target,
        "reviews": 0,
    }

    python_exe = find_python()
    if not python_exe:
        log("Python 3.11+ not found", "ERROR")
        result["error"] = "python_not_found"
        emit_result(result)
        close_log_file()
        return EXIT_ENV
    log(f"python (mitmdump launcher): {python_exe}")
    mitm_mode, mitm_path = find_mitmdump(python_exe)
    if not mitm_mode:
        log("mitmproxy not found. Run: pip install mitmproxy", "ERROR")
        result["error"] = "mitmproxy_not_found"
        emit_result(result)
        close_log_file()
        return EXIT_ENV
    if not os.path.isfile(mitmproxy_cert_path()):
        log("WARNING: mitmproxy CA cert not found; run mitmdump once and install the cert", "WARN")
    elif not cert_is_trusted():
        log("WARNING: mitmproxy CA cert not in system trust store; HTTPS capture may fail", "WARN")

    log(f"store={store_name} id={store_id} target={args.target} port={args.port}")
    log(f"capture_file={capture_file}")
    log(f"python={python_exe} mitm_mode={mitm_mode}")

    addon_path = None
    mitm = None
    proxy_on = False
    exit_code = EXIT_ERROR
    prog = {"offset": 0, "mids": set(), "max_start": 0, "is_end": False,
            "shop_review_count": None}

    def cleanup():
        nonlocal proxy_on
        if proxy_on:
            try:
                set_system_proxy(False, args.port)
                log("system proxy disabled")
            except Exception as ex:
                log(f"failed to disable proxy: {ex}", "ERROR")
            proxy_on = False
        if mitm and mitm.poll() is None:
            try:
                mitm.terminate()
                mitm.wait(timeout=5)
            except Exception:
                try:
                    mitm.kill()
                except Exception:
                    pass
        if addon_path:
            try:
                os.unlink(addon_path)
            except OSError:
                pass

    def on_signal(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, on_signal)
    try:
        signal.signal(signal.SIGTERM, on_signal)
    except Exception:
        pass

    try:
        kill_port(args.port)
        addon_path = write_addon()
        env = os.environ.copy()
        env["DP_CAPTURE_FILE"] = capture_file
        env["PYTHONIOENCODING"] = "utf-8"

        mitm_err_log = os.path.join(out_dir, f"{store_id}_mitmdump.err.log")
        err_fp = open(mitm_err_log, "wb")
        mitm_args = ["-p", str(args.port), "-s", addon_path,
                     "--set", "flow_detail=4", "--set", "ssl_insecure=true"]
        if mitm_mode == "exe":
            cmdline = [mitm_path] + mitm_args
        else:
            cmdline = [mitm_path, "-c",
                       "import sys;sys.argv=['mitmdump']+" + repr(mitm_args) +
                       ";from mitmproxy.tools.main import mitmdump;mitmdump()"]
        log("starting mitmdump ...")
        mitm = subprocess.Popen(cmdline, stdout=subprocess.DEVNULL,
                                stderr=err_fp, env=env)
        if not wait_port("127.0.0.1", args.port, timeout=30):
            log(f"mitmdump failed to start, see {mitm_err_log}", "ERROR")
            result["error"] = "mitmdump_start_failed"
            emit_result(result)
            return EXIT_ENV
        log("mitmdump ready")

        set_system_proxy(True, args.port)
        proxy_on = True
        log("system proxy enabled")
        time.sleep(1)

        cnt, _ = count_reviews(capture_file, prog)
        baseline_size = os.path.getsize(capture_file) if os.path.exists(capture_file) else 0
        log(f"baseline: {cnt} reviews already in file, {baseline_size} bytes")

        log("=" * 50)
        log(f"  Please open the review list page of [{store_name}] in WeChat now.")
        log(f"  Waiting up to {args.wait_timeout}s for the first review-list request ...")
        log("=" * 50)

        if not wait_for_first_request(capture_file, baseline_size, args.wait_timeout):
            log(f"timeout ({args.wait_timeout}s): no review-list request detected", "ERROR")
            result["status"] = "timeout"
            result["error"] = "no_review_request"
            result["reviews"] = count_reviews(capture_file, prog)[0]
            emit_result(result)
            return EXIT_TIMEOUT
        log("review-list request detected! start scrolling")

        if args.no_scroll:
            log("--no-scroll set, skip auto scroll")
        else:
            if args.cursor_pos:
                try:
                    cx, cy = [int(v) for v in args.cursor_pos.split(",")]
                    import ctypes
                    ctypes.windll.user32.SetCursorPos(cx, cy)
                    log(f"cursor moved to ({cx},{cy}) from --cursor-pos")
                except Exception as ex:
                    log(f"bad --cursor-pos: {ex}", "WARN")
            else:
                try:
                    wnd = find_target_window(args.window_keyword
                                             or ["大众点评", "美食电影", "dianping"])
                except ImportError:
                    wnd = None
                    log("win32gui unavailable in this process; window auto-locate "
                        "disabled, keep the cursor on the review list yourself", "WARN")
                if wnd:
                    _, title, rect = wnd
                    cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
                    import ctypes
                    ctypes.windll.user32.SetCursorPos(cx, cy)
                    log(f"window found: {title}, cursor -> ({cx},{cy})")
                else:
                    log("target window not found; scrolling at current cursor position", "WARN")

            prev_cnt = cnt
            stall = 0
            stop_reason = None
            for i in range(args.max_scroll):
                scroll_down(args.scroll_clicks)
                time.sleep(args.scroll_interval)
                if (i + 1) % args.check_every == 0:
                    cnt, max_s = count_reviews(capture_file, prog)
                    delta = cnt - prev_cnt
                    log(f"scroll {i+1}/{args.max_scroll} | reviews {cnt}"
                        + (f"/{args.target}" if args.target else "")
                        + f" | +{delta}"
                        + (" | isEnd=True" if prog["is_end"] else ""))
                    if prog["is_end"]:
                        log("API returned isEnd=true: reached the end of the review list")
                        stop_reason = "end_reached"
                        break
                    if args.target and cnt >= args.target:
                        log(f"target reached: {cnt} >= {args.target}")
                        stop_reason = "target_reached"
                        break
                    if delta == 0:
                        stall += 1
                        if stall >= args.stall_limit:
                            log(f"no new reviews after {stall} checks; likely reached the end")
                            stop_reason = "stalled"
                            break
                    else:
                        stall = 0
                    prev_cnt = cnt
            if stop_reason is None:
                stop_reason = "max_scroll"

        cnt, max_s = count_reviews(capture_file, prog)
        result["reviews"] = cnt
        result["max_start"] = max_s
        result["is_end"] = prog["is_end"]
        result["shop_review_count"] = prog["shop_review_count"]
        result["stop_reason"] = stop_reason if not args.no_scroll else "no_scroll"
        if prog["is_end"] or (args.target and cnt >= args.target):
            result["status"] = "ok"
            exit_code = EXIT_OK
        else:
            result["status"] = "partial"
            exit_code = EXIT_PARTIAL
        log(f"capture finished: {cnt} reviews (stop: {result['stop_reason']})")
        emit_result(result)
        return exit_code

    except KeyboardInterrupt:
        log("interrupted by user")
        cnt, _ = count_reviews(capture_file, prog)
        result["status"] = "interrupted"
        result["reviews"] = cnt
        emit_result(result)
        return EXIT_PARTIAL
    except Exception as ex:
        log(f"unexpected error: {ex}", "ERROR")
        result["error"] = str(ex)
        emit_result(result)
        return EXIT_ERROR
    finally:
        cleanup()
        close_log_file()

# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
CSV_HEADERS = ["org_code", "store_name", "username", "review_date", "rating",
               "price_per_person", "content", "sentiment", "store_feedback"]


def parse_review_time(t):
    if not t:
        return None
    t = str(t).strip()
    now = datetime.now()
    today = now.date()
    m = re.match(r"^(\d+)天前$", t)
    if m:
        return today - timedelta(days=int(m.group(1)))
    m = re.match(r"^(\d+)(小时|分钟)前$", t)
    if m:
        return today
    if "昨天" in t:
        return today - timedelta(days=1)
    if "前天" in t:
        return today - timedelta(days=2)
    m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日", t)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})月(\d{1,2})日", t)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        try:
            dt = datetime(now.year, mo, d).date()
        except ValueError:
            return None
        if dt > today:
            dt = datetime(now.year - 1, mo, d).date()
        return dt
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            return None
    return None


def classify_sentiment(star10):
    """star10: 0-5 scale (star/10)."""
    if star10 >= 4.0:
        return "好评"
    if star10 >= 3.0:
        return "中评"
    return "差评"


def parse_price(p):
    if p is None:
        return ""
    s = str(p).strip()
    if not s or s == "0":
        return ""
    nums = re.findall(r"\d+", s)
    return nums[0] if nums else ""


def walk_rich_text(node):
    """Flatten dianping rich-text JSON ({node,children:[{type:'text',text},...]}) to plain text."""
    if isinstance(node, dict):
        if node.get("type") == "text" and node.get("text"):
            return str(node["text"])
        return "".join(walk_rich_text(c) for c in (node.get("children") or []))
    if isinstance(node, list):
        return "".join(walk_rich_text(c) for c in node)
    if isinstance(node, str):
        return node
    return ""


def reply_plain_text(content):
    """Reply content may be a plain string or rich-text JSON string."""
    if isinstance(content, str) and content.strip().startswith("{"):
        try:
            return walk_rich_text(json.loads(content)).strip()
        except Exception:
            return content.strip()
    if isinstance(content, (dict, list)):
        return walk_rich_text(content).strip()
    return (content or "").strip() if isinstance(content, str) else ""


def normalize_reply_date(t):
    """replyTime samples: '2026-8-17' -> '2026-08-17'; pass through anything else."""
    if not t:
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", str(t))
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return str(t)


def collect_reply_api_replies(fh, reply_map):
    """Parse paginateDpFeedReply responses.

    The response entry's URL carries the request's `mainId` query param, which
    links the reply page to its review. reply_map: mainId -> {replyKey: dict}."""
    n = 0
    for line in fh:
        try:
            e = json.loads(line)
        except Exception:
            continue
        if not isinstance(e, dict) or e.get("type") != "response":
            continue
        if REPLY_API_KEYWORD not in e.get("url", ""):
            continue
        m = re.search(r"[?&]mainId=(\d+)", e.get("url", ""))
        if not m:
            continue
        mid = m.group(1)
        body = e.get("body") or ""
        if not body:
            continue
        try:
            data = json.loads(body)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        r = data.get("result")
        if not isinstance(r, dict):
            continue
        recs = r.get("records") or []
        bucket = reply_map.setdefault(mid, {})
        for rec in recs:
            if not isinstance(rec, dict):
                continue
            fu = rec.get("fromUser") or {}
            text = reply_plain_text(rec.get("content"))
            if not text:
                continue
            key = str(rec.get("feedReplyIdL") or rec.get("feedReplyId")
                      or f"{fu.get('userName')}|{text[:50]}")
            if key in bucket:
                continue
            bucket[key] = {
                "user_type": fu.get("userType"),
                "user_name": fu.get("userName") or "",
                "time": normalize_reply_date(rec.get("replyTime")),
                "text": text,
            }
            n += 1
    return n


def extract_reviews_from_jsonl(capture_file, org_code, store_name, rating_scale):
    """Parse capture file into review rows.

    Handles BOTH review-list response formats (tab-dependent, not store-dependent):
      - data.list            -> "最新" (latest) tab, e.g. outsidesiftedreviewlist.bin
      - data.result.reviewList -> "全部" (all) tab
    Merchant replies are merged from two sources:
      - comments[] embedded in list responses (PRIMARY source, full content,
        but no reply time) -- obtained by simply scrolling the list
      - paginateDpFeedReply responses (same content, adds replyTime) -- only
        captured if the operator clicks into review detail pages
    Returns (rows, stats). Dedupe by mainId."""

    reply_map = {}
    with open(capture_file, "r", encoding="utf-8", errors="ignore") as fh:
        reply_total = collect_reply_api_replies(fh, reply_map)
    reply_map = {mid: b for mid, b in reply_map.items() if b}

    reviews = {}
    embedded_merchant = 0
    with open(capture_file, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if not isinstance(e, dict) or e.get("type") != "response":
                continue
            if REVIEW_API_KEYWORD not in e.get("url", ""):
                continue
            body = e.get("body", "")
            if not body:
                continue
            try:
                data = json.loads(body)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            r = data.get("result")
            r = r if isinstance(r, dict) else {}
            lst = data.get("list") or r.get("reviewList", []) or []
            for item in lst:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("mainId", ""))
                if not mid or mid in reviews:
                    continue
                fu = item.get("feedUser") or {}
                username = fu.get("userName") or fu.get("nickName") \
                    or item.get("userName") or "匿名用户"
                star_raw = item.get("star") or item.get("score") or 0
                try:
                    star_num = float(star_raw)
                except (TypeError, ValueError):
                    star_num = 0.0
                star10 = star_num / 10.0
                if rating_scale == "ten":
                    rating_val = f"{star10:.1f}"
                else:
                    rating_val = str(int(star_num)) if star_num == int(star_num) else str(star_num)
                dt = parse_review_time(item.get("time") or "")
                feedback_parts = []
                seen_texts = set()
                # source 1: reply API version (same content as embedded, adds time)
                for rr in (reply_map.get(mid) or {}).values():
                    if rr["user_type"] != MERCHANT_USER_TYPE or not rr["text"]:
                        continue
                    if rr["text"] in seen_texts:
                        continue
                    seen_texts.add(rr["text"])
                    name = rr["user_name"] or "商家回应"
                    head = f"[{name} {rr['time']}]" if rr["time"] else f"[{name}]"
                    feedback_parts.append(f"{head}{rr['text']}")
                # source 2: replies embedded in the list response (primary source)
                for c in (item.get("comments") or []):
                    if not isinstance(c, dict):
                        continue
                    cfu = c.get("fromUser") or {}
                    if cfu.get("userType") != MERCHANT_USER_TYPE:
                        continue
                    ctext = reply_plain_text(c.get("content"))
                    if not ctext or ctext in seen_texts:
                        continue
                    seen_texts.add(ctext)
                    embedded_merchant += 1
                    uname = cfu.get("userName") or "商家回应"
                    feedback_parts.append(f"[{uname}]{ctext}")
                reviews[mid] = {
                    "org_code": org_code,
                    "store_name": store_name,
                    "username": str(username)[:50],
                    "review_date": dt.strftime("%Y-%m-%d") if dt else "",
                    "rating": rating_val,
                    "price_per_person": parse_price(item.get("price") or item.get("avgPrice")
                                                    or item.get("avgPriceText")),
                    "content": item.get("content") or item.get("reviewBody") or "",
                    "sentiment": classify_sentiment(star10),
                    "store_feedback": "\n".join(feedback_parts),
                }
    merchant_from_api = sum(
        1 for b in reply_map.values() for r in b.values()
        if r["user_type"] == MERCHANT_USER_TYPE)
    stats = {
        "merchant_replies_from_reply_api": merchant_from_api,
        "merchant_replies_embedded_extra": embedded_merchant,
        "reply_api_mainIds": len(reply_map),
    }
    return list(reviews.values()), stats


def cmd_export(args):
    capture_file = args.input
    if not capture_file:
        capture_file = os.path.join(os.getcwd(), f"{args.store_id}_capture.jsonl")
    if not os.path.isfile(capture_file):
        emit_result({"status": "error", "error": f"capture file not found: {capture_file}"})
        return EXIT_ERROR

    items, reply_stats = extract_reviews_from_jsonl(capture_file, args.org_code,
                                                    args.store_name, args.rating_scale)
    items.sort(key=lambda x: (x["review_date"] == "", x["review_date"] or "9999"),
               reverse=True)

    out = args.output
    if not out:
        base = os.path.splitext(os.path.basename(capture_file))[0].replace("_capture", "")
        ext = "csv" if args.format == "csv" else "json"
        out = os.path.join(os.path.dirname(os.path.abspath(capture_file)),
                           f"{base}_reviews.{ext}")
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)

    if args.format == "json":
        with open(out, "w", encoding="utf-8") as fp:
            json.dump(items, fp, ensure_ascii=False, indent=2)
    else:
        enc = args.encoding
        with open(out, "w", encoding=enc, errors="replace", newline="") as fp:
            w = csv.DictWriter(fp, fieldnames=CSV_HEADERS)
            w.writeheader()
            w.writerows(items)

    dist = {}
    with_feedback = 0
    for it in items:
        dist[it["sentiment"]] = dist.get(it["sentiment"], 0) + 1
        if it["store_feedback"]:
            with_feedback += 1
    result = {"status": "ok", "rows": len(items), "output": os.path.abspath(out),
              "format": args.format, "sentiment": dist, "source": os.path.abspath(capture_file),
              "reviews_with_replies": with_feedback,
              "reply_stats": reply_stats}
    if with_feedback == 0 and len(items) > 0:
        result["warning"] = ("no merchant replies found in any review -- replies come "
                             "from the review list itself (comments[]), so verify the "
                             "store actually has 商家回复 on the dianping page")
    if _JSON_MODE:
        emit_result(result)
    else:
        log(f"exported {len(items)} reviews -> {out}")
        for k in ("好评", "中评", "差评"):
            log(f"  {k}: {dist.get(k, 0)}")
        log(f"  reviews with merchant replies: {with_feedback}")
        log(f"  replies from reply API: {reply_stats['merchant_replies_from_reply_api']}, "
            f"embedded extras: {reply_stats['merchant_replies_embedded_extra']}")
        emit_result(result)
    return EXIT_OK

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="dianping_scraper",
        description="Dianping review scraper (agent-friendly CLI).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--json", action="store_true",
                        help="print single-line JSON result on stdout; logs go to stderr")
        sp.add_argument("--quiet", action="store_true", help="suppress stderr logs")

    sp = sub.add_parser("check-env", help="verify python/mitmproxy/cert/pywin32")
    add_common(sp)

    sp = sub.add_parser("capture", help="capture reviews via mitmproxy + auto scroll")
    sp.add_argument("store_name", help="store name, e.g. 'FengYu (Huaihai)'")
    sp.add_argument("store_id", help="store id / org code, e.g. 080501")
    sp.add_argument("--target", type=int, default=None,
                    help="stop when unique review count reaches N; omit to run until isEnd/max-scroll (recommended)")
    sp.add_argument("--max-scroll", type=int, default=3000, help="max scroll rounds (default 3000)")
    sp.add_argument("--port", type=int, default=8888, help="proxy port (default 8888)")
    sp.add_argument("--scroll-clicks", type=int, default=10, help="wheel clicks per round (default 10)")
    sp.add_argument("--scroll-interval", type=float, default=1.0, help="seconds between rounds (default 1.0)")
    sp.add_argument("--check-every", type=int, default=20, help="count reviews every N rounds (default 20)")
    sp.add_argument("--stall-limit", type=int, default=3, help="stop after N checks without new reviews (default 3)")
    sp.add_argument("--wait-timeout", type=int, default=180, help="seconds to wait for first review-list request (default 180)")
    sp.add_argument("--output-dir", help="directory for capture/log files (default: cwd)")
    sp.add_argument("--capture-file", help="explicit capture jsonl path")
    sp.add_argument("--log-file", help="explicit log file path")
    sp.add_argument("--no-scroll", action="store_true", help="capture only, no auto scroll")
    sp.add_argument("--cursor-pos", help="explicit 'x,y' cursor position for scrolling")
    sp.add_argument("--window-keyword", action="append",
                    help="window title keyword to locate mini-program window (repeatable)")
    add_common(sp)

    sp = sub.add_parser("export", help="export captured jsonl to store_reviews CSV/JSON")
    sp.add_argument("store_id", help="store id; used to locate {store_id}_capture.jsonl if --input omitted")
    sp.add_argument("--input", help="capture jsonl path (default: ./{store_id}_capture.jsonl)")
    sp.add_argument("--org-code", required=True, help="org_code value written to output")
    sp.add_argument("--store-name", required=True, help="store_name value written to output")
    sp.add_argument("--output", help="output file path (default: alongside input)")
    sp.add_argument("--format", choices=["csv", "json"], default="csv", help="output format (default csv)")
    sp.add_argument("--encoding", default="utf-8-sig",
                    help="csv encoding: utf-8-sig (Excel-friendly, default), utf-8, gbk")
    sp.add_argument("--rating-scale", choices=["raw", "ten"], default="ten",
                    help="ten: convert to 0.0..5.0 stars (default, matches DB); raw: keep 0..50 as-is")
    add_common(sp)
    return p


def main():
    global _QUIET, _JSON_MODE
    parser = build_parser()
    args = parser.parse_args()
    _QUIET = getattr(args, "quiet", False)
    _JSON_MODE = getattr(args, "json", False)

    handlers = {"check-env": cmd_check_env, "capture": cmd_capture, "export": cmd_export}
    handler = handlers[args.command]
    try:
        code = handler(args)
    except Exception as ex:
        if _JSON_MODE:
            emit_result({"status": "error", "error": str(ex)})
        else:
            log(f"fatal: {ex}", "ERROR")
        code = EXIT_ERROR
    sys.exit(code)


if __name__ == "__main__":
    main()
