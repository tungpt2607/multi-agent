#!/usr/bin/env python3
"""Cổng duyệt + Guard lớp 1 (bản vá REQ-0002, Gói 1; mở rộng REQ-0004): FAIL CLOSED.

Chạy như hook PreToolUse. Đọc JSON từ stdin; in quyết định deny ra stdout.
Không in gì (exit 0) = cho qua (luồng quyền thường của Claude Code vẫn áp dụng).
Mọi lỗi (JSON hỏng, ngoại lệ, shlex lỗi, cú pháp lạ) => deny.

Bash: allowlist theo token (không denylist regex). push/merge/MCP ghi: cần phiếu
approve_action gắn "vân tay" do gate tự tính từ lệnh, bằng chứng chỉ lấy từ ledger
(do record_answer.py ghi), mỗi phiếu dùng một lần (ledger/consumed.jsonl).

REQ-0004: thêm allowlist npm/pnpm CỐ ĐỊNH (PKG_ALLOW, khớp toàn bộ argv, không đối số thêm,
phải đứng một mình trong lệnh, bị cấm khi có .npmrc) và cấm executor ghi .gitignore ở gốc repo.
"""
import fnmatch
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
MAIN = "orchestrator"  # phiên chính (không có agent_type)
MCP_PREFIX = "mcp__github__"
YES = "Đồng ý"  # nhãn ô bấm phải bắt đầu bằng chuỗi này

# Tool MCP github chỉ đọc (liệt kê cụ thể). Tên khác / tên lạ = coi là GHI, cần phiếu.
MCP_READ = {
    "get_me", "get_label", "get_latest_release", "get_release_by_tag", "get_tag",
    "get_team_members", "get_teams", "get_commit", "get_file_contents", "get_job_logs",
    "get_check_run", "issue_read", "pull_request_read", "actions_get", "actions_list",
    "list_issue_fields", "list_issue_types", "list_issues", "list_pull_requests",
    "list_branches", "list_commits", "list_tags", "list_releases", "list_notifications",
    "search_code", "search_issues", "search_pull_requests", "search_repositories",
    "search_users", "search_orgs",
}

# Allowlist Bash (ART-0022 Điểm 1)
ALLOW = {"ls", "pwd", "cat", "head", "tail", "wc", "grep", "rg", "find", "stat", "file", "diff",
         "sort", "uniq", "cut", "jq", "echo", "printf", "date", "true", "test",
         "git", "pytest", "python3"}
BANNED = {"eval", "exec", "source", ".", "bash", "sh", "zsh", "dash", "sudo", "xargs", "env",
          "printenv", "nohup", "command", "builtin", "time", "timeout", "nice"}
# REQ-0004: npm/pnpm chỉ đúng các argv này (so khớp toàn bộ, không tiền tố, không đối số thêm).
# Cố ý KHÔNG có: npx, node, yarn, corepack, install/i/ci/add/exec/dlx/x, run <tên khác>, npm t,
# pnpm run <x>, pnpm build:web, cờ --, --filter, -w, -r, --prefix.
PKG_ALLOW = {
    ("pnpm", "test"), ("pnpm", "lint"), ("pnpm", "typecheck"), ("pnpm", "build"),
    ("npm", "test"), ("npm", "run", "lint"), ("npm", "run", "typecheck"),
    ("npm", "run", "build"), ("npm", "run", "build:web"),
}
PKG_CMDS = ("npm", "pnpm")
PKG_NPMRC_GLOBS = ("apps/*/.npmrc", "packages/*/.npmrc")
NO_PATH_CHECK = {"echo", "printf", "true", "date", "pwd"}  # không đọc file theo đối số
GIT_READ = {"status", "diff", "log", "show", "rev-parse", "merge-base", "ls-files", "blame"}
SECRET = re.compile(r"\.env|id_rsa|id_ed25519|id_ecdsa|\.pem\b|\.netrc|\.ssh\b|\.aws\b", re.I)
SECRET_SAMPLES = [".env", ".env.local", ".envrc", "id_rsa", "id_ed25519", "id_ecdsa",
                  "server.pem", ".netrc"]
PUNCT = re.compile(r"^[();<>|&]+$")
BRANCH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")
HEREDOC_A = re.compile(r"<<(['\"])([A-Za-z_]\w*)\1\n")          # git commit -F - <<'EOF'
COMMIT_F = re.compile(r"git\s+commit\b[^\"'\\]*\s-F\s+-\s*")
HEREDOC_B = re.compile(r"\"\$\(cat <<'([A-Za-z_]\w*)'\n")        # -m "$(cat <<'EOF' ... )"
COMMIT_M = re.compile(r"git\s+commit\b.*\s-m\s+")
PYTEST_OPT = re.compile(
    r"-q|-qq|-v|-vv|-vvv|-x|-s|-l|-ra|-rA|-rf|-rs|-rx|--lf|--ff|--nf|--collect-only|--co|"
    r"--no-header|--disable-warnings|--tb=(auto|long|short|line|native|no)|"
    r"--maxfail=\d+|--durations=\d+")


# Whitelist tuỳ chọn theo từng lệnh (so khớp chính xác, không prefix/viết tắt).
# (cờ ngắn không giá trị, cờ ngắn có giá trị, {tuỳ chọn dài: số đối số tách rời theo sau})
# Dạng --x=giá_trị được nhận khi tên --x nằm trong whitelist. Mọi tuỳ chọn khác = deny.
# Cố ý KHÔNG có: grep/rg -r -R -u --hidden --no-ignore --follow -f --pre, diff -r,
# sort -o/--output/--compress-program/-T, file -C/-m/-f, date -s/-f, jq -f/--rawfile/--slurpfile/-L.
OPTS = {
    "ls": ("aAlhtrSd1FpsiGL", "", {"--all": 0, "--almost-all": 0, "--human-readable": 0, "--classify": 0,
                                    "--directory": 0, "--reverse": 0, "--size": 0, "--inode": 0}),
    "pwd": ("LP", "", {}),
    "cat": ("nbsEATv", "", {"--number": 0, "--number-nonblank": 0, "--squeeze-blank": 0, "--show-ends": 0,
                            "--show-tabs": 0, "--show-all": 0, "--show-nonprinting": 0}),
    "head": ("qv0123456789", "nc", {"--lines": 1, "--bytes": 1, "--quiet": 0, "--silent": 0, "--verbose": 0}),
    "tail": ("qv0123456789", "nc", {"--lines": 1, "--bytes": 1, "--quiet": 0, "--silent": 0, "--verbose": 0}),
    "wc": ("lwcmL", "", {"--lines": 0, "--words": 0, "--bytes": 0, "--chars": 0, "--max-line-length": 0}),
    "grep": ("inwvcLlHhEFGPaIqsxbo", "ABCme",
             {"--ignore-case": 0, "--line-number": 0, "--word-regexp": 0, "--invert-match": 0, "--count": 0,
              "--files-with-matches": 0, "--files-without-match": 0, "--with-filename": 0,
              "--no-filename": 0, "--extended-regexp": 0, "--fixed-strings": 0, "--basic-regexp": 0,
              "--perl-regexp": 0, "--only-matching": 0, "--quiet": 0, "--silent": 0, "--no-messages": 0,
              "--text": 0, "--line-regexp": 0, "--color": 0, "--max-count": 1, "--after-context": 1,
              "--before-context": 1, "--context": 1, "--regexp": 1}),
    "rg": ("inwvclHFSsoqN", "ABCmet",
           {"--ignore-case": 0, "--line-number": 0, "--word-regexp": 0, "--invert-match": 0, "--count": 0,
            "--files-with-matches": 0, "--with-filename": 0, "--no-filename": 0, "--fixed-strings": 0,
            "--smart-case": 0, "--case-sensitive": 0, "--only-matching": 0, "--quiet": 0,
            "--no-line-number": 0, "--color": 0, "--max-count": 1, "--after-context": 1,
            "--before-context": 1, "--context": 1, "--regexp": 1, "--type": 1}),
    "stat": ("tL", "c", {"--terse": 0, "--dereference": 0, "--format": 1, "--printf": 1}),
    "file": ("bLiN", "", {"--brief": 0, "--mime": 0, "--mime-type": 0, "--mime-encoding": 0,
                          "--dereference": 0, "--no-pad": 0}),
    "diff": ("uqbBiwcyNs", "UC", {"--unified": 0, "--brief": 0, "--ignore-case": 0,
                                  "--ignore-space-change": 0, "--ignore-all-space": 0,
                                  "--ignore-blank-lines": 0, "--side-by-side": 0, "--context": 0,
                                  "--report-identical-files": 0}),
    "sort": ("nrubfdghMVscm", "kt", {"--numeric-sort": 0, "--reverse": 0, "--unique": 0,
                                     "--ignore-leading-blanks": 0, "--ignore-case": 0,
                                     "--dictionary-order": 0, "--stable": 0, "--check": 0, "--merge": 0,
                                     "--human-numeric-sort": 0, "--version-sort": 0, "--month-sort": 0,
                                     "--key": 1, "--field-separator": 1}),
    "uniq": ("cdui", "fsw", {"--count": 0, "--repeated": 0, "--unique": 0, "--ignore-case": 0}),
    "cut": ("s", "dfcb", {"--only-delimited": 0, "--delimiter": 1, "--fields": 1, "--characters": 1,
                          "--bytes": 1}),
    "jq": ("rcsenRSCMaj", "", {"--raw-output": 0, "--compact-output": 0, "--slurp": 0, "--null-input": 0,
                               "--exit-status": 0, "--sort-keys": 0, "--tab": 0, "--raw-input": 0,
                               "--ascii-output": 0, "--color-output": 0, "--monochrome-output": 0,
                               "--join-output": 0, "--arg": 2, "--argjson": 2, "--indent": 1}),
    "date": ("u", "d", {"--utc": 0, "--universal": 0, "--date": 1, "--iso-8601": 0, "--rfc-3339": 0,
                        "--rfc-email": 0}),
}
FIND_VAL = {"-name", "-iname", "-type", "-path", "-ipath", "-maxdepth", "-mindepth", "-size", "-mtime",
            "-newer", "-perm"}
FIND_FLAG = {"-not", "!", "-and", "-or", "-a", "-o", "-print", "-print0", "-prune", "-empty", "(", ")"}
MCP_PR = {"merge_pull_request", "update_pull_request", "update_pull_request_branch",
          "pull_request_review_write", "request_copilot_review", "add_comment_to_pending_review",
          "add_reply_to_pull_request_comment"}
MCP_FILE = {"push_files", "create_or_update_file", "delete_file"}
MCP_VAL = re.compile(r"[\w.\-/]+")


class BashDeny(Exception):
    pass


# ---------------------------------------------------------------- tiện ích chung
def out_deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"[gate] {reason}",
    }}, ensure_ascii=False))
    sys.exit(0)


def deny(reason, ctx):
    log(ctx, "deny", reason)
    out_deny(reason)


def allow(ctx):
    log(ctx, "pass", "")
    sys.exit(0)


def log(ctx, decision, reason):
    try:
        p = ctx["root"] / ".agents" / "state" / "tool_log.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "agent": ctx["agent"], "tool": ctx["tool"], "target": ctx["target"],
                "decision": decision, "reason": reason,
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def user_answered(root, dec_id):
    """Có bản ghi ô bấm (do hook record_answer.py ghi) mà câu hỏi chứa mã phiếu và có câu trả lời."""
    p = root / ".agents" / "ledger" / "answers.jsonl"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if f"[{dec_id}]" in json.dumps(rec.get("questions"), ensure_ascii=False) and rec.get("response"):
            return True
    return False


def write_mode(root):
    """draft: chỉ ghi drafts/<REQ>/ · branch: ghi code thật nhưng chỉ trên nhánh agent/<REQ>."""
    try:
        m = re.search(r"^\s*mode:\s*(draft|branch)\b", (root / ".agents" / "workflow.yaml").read_text(encoding="utf-8"), re.M)
    except OSError:
        m = None
    return m.group(1) if m else "draft"


def current_branch(root):
    try:
        head = (root / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else None


def risk_report_ok(root, dec):
    """risk_report phải là Artifact type risk_report, do guard tạo, cùng request với phiếu. Trả lý do lỗi hoặc None."""
    rr = dec.get("risk_report")
    if not isinstance(rr, str) or not re.fullmatch(r"ART-\d{4}", rr):
        return "phiếu thiếu risk_report"
    art = load(root / ".agents" / "artifacts" / f"{rr}.json")
    if not art:
        return f"không tìm thấy {rr}"
    req = dec.get("request_id")
    if not isinstance(req, str) or not re.fullmatch(r"REQ-\d{4}", req):
        return "phiếu thiếu request_id hợp lệ"
    if art.get("type") != "risk_report" or art.get("created_by") != "guard" or art.get("request_id") != req:
        return f"{rr} không phải risk_report của guard cùng request {req}"
    return None


# ---------------------------------------------------------------- ghi file (giữ nguyên luật cũ)
EXECUTOR_PROTECTED = (".agents/", ".claude/", ".github/", ".git/")
EXECUTOR_PROTECTED_FILES = ("CLAUDE.md", ".gitignore")  # REQ-0004: thêm .gitignore (chỉ ở gốc repo)


def executor_approved(root, rel):
    """Thực thi chỉ được ghi khi task mang mã quyết định người dùng đã duyệt (kèm risk_report), đúng chỗ cho phép."""
    active = load(root / ".agents" / "state" / "active_task.json")
    if not active or active.get("agent") != "executor":
        return "chưa có task Thực thi đang chạy (state/active_task.json)"
    dec_id = active.get("approved_by_decision")
    if not dec_id:
        return "task chưa mang mã quyết định của người dùng"
    dec = load(root / ".agents" / "decisions" / f"{dec_id}.json")
    if not dec:
        return f"không tìm thấy {dec_id}"
    ans = dec.get("answer") or {}
    if dec.get("status") != "answered" or ans.get("action") not in ("choose", "choose_modified"):
        return f"{dec_id} chưa được người dùng duyệt"
    if not user_answered(root, dec_id):
        return f"không thấy câu trả lời gốc của người dùng cho {dec_id} trong .agents/ledger/"
    if dec.get("kind") != "approve_action":
        return f"{dec_id} không phải phiếu approve_action"
    why = risk_report_ok(root, dec)
    if why:
        return f"{dec_id}: {why} (thiếu bước guard trước khi thực thi)"
    req = active.get("request_id", "")
    if not re.fullmatch(r"REQ-\d{4}", req):
        return "request_id của task không hợp lệ"
    if dec.get("request_id") != req:
        return f"{dec_id} thuộc request khác"
    if rel in EXECUTOR_PROTECTED_FILES and rel != "CLAUDE.md":
        # .gitignore ở gốc repo: mọi chế độ đều cấm (bản nháp drafts/<REQ>/.../.gitignore không dính vì rel khác)
        return "Thực thi không được ghi .gitignore ở gốc repo"
    if write_mode(root) == "branch":
        if current_branch(root) != f"agent/{req}":
            return f"chỉ được ghi code khi đang ở nhánh agent/{req} (Điều phối tạo nhánh sau khi bạn duyệt)"
        if rel.startswith(EXECUTOR_PROTECTED) or rel in EXECUTOR_PROTECTED_FILES:
            return "Thực thi không được ghi vào .agents/, .claude/, .github/, CLAUDE.md, .gitignore (gốc)"
        return None
    if not rel.startswith(f"drafts/{req}/"):
        return f"chế độ nháp: chỉ được ghi trong drafts/{req}/"
    return None


def check_write(ctx, rel):
    agent, root = ctx["agent"], ctx["root"]
    if rel is None:
        deny("ghi ra ngoài thư mục dự án", ctx)
    if rel.startswith(".claude/") or rel in ("CLAUDE.md",) or rel.startswith(".agents/schemas/") \
            or rel == ".agents/workflow.yaml":
        deny("không agent nào được sửa luật, hook, schema hay quy trình; người dùng tự sửa", ctx)
    if rel.startswith(".agents/ledger/"):
        deny("sổ câu trả lời gốc chỉ do hook ghi, không agent nào được sửa", ctx)
    if rel.startswith(".agents/knowledge/"):
        deny("kho tri thức chỉ người dùng ghi; agent chỉ đề xuất", ctx)
    if rel.startswith(".agents/decisions/"):
        if agent != MAIN:
            deny("chỉ Điều phối được ghi sổ quyết định", ctx)
        existing = load(root / rel)
        if existing and existing.get("status") != "open":
            deny("phiếu đã trả lời thì bị khoá, không sửa được", ctx)
        allow(ctx)
    if rel.startswith((".agents/requests/", ".agents/tasks/", ".agents/state/")):
        if agent != MAIN:
            deny("chỉ Điều phối được ghi trạng thái", ctx)
        allow(ctx)
    if rel.startswith(".agents/artifacts/"):
        allow(ctx)
    if agent == "executor":
        why = executor_approved(root, rel)
        if why:
            deny(why, ctx)
        allow(ctx)
    deny("chỉ agent Thực thi được ghi code/bản nháp, và chỉ sau khi bạn duyệt", ctx)


# ---------------------------------------------------------------- phiếu push/merge/MCP (chỉ tin ledger)
def consumed_set(root):
    p = root / ".agents" / "ledger" / "consumed.jsonl"
    if not p.exists():
        return set()
    used = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            used.add(json.loads(line)["dec"])  # dòng hỏng -> ngoại lệ -> deny
    return used


def find_ticket(root, fp):
    """Tìm phiếu hợp lệ cho vân tay fp. Trả (dec_id, None) hoặc (None, lý do)."""
    marker = "{{" + fp + "}}"
    try:
        lines = (root / ".agents" / "ledger" / "answers.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None, f"chưa có sổ câu trả lời gốc; cần phiếu approve_action chứa {marker}"
    cands = []
    for line in lines:
        try:
            rec = json.loads(line)
            ans = rec["response"]["answers"]
        except (ValueError, KeyError, TypeError):
            continue
        if not isinstance(ans, dict):
            continue
        for q, label in ans.items():
            if isinstance(q, str) and isinstance(label, str) and marker in q and label.startswith(YES):
                m = re.match(r"\s*\[(DEC-\d{4})\]", q)
                if m:
                    cands.append(m.group(1))
    if not cands:
        return None, f"chưa có câu trả lời \"{YES}...\" của bạn cho phiếu chứa {marker}"
    used = consumed_set(root)
    why = []
    for dec_id in dict.fromkeys(cands):
        if dec_id in used:
            why.append(f"{dec_id} đã dùng một lần rồi")
            continue
        dec = load(root / ".agents" / "decisions" / f"{dec_id}.json")
        if not dec:
            why.append(f"không tìm thấy {dec_id}")
        elif dec.get("kind") != "approve_action":
            why.append(f"{dec_id} không phải approve_action")
        elif dec.get("status") != "answered" or (dec.get("answer") or {}).get("action") not in ("choose", "choose_modified"):
            why.append(f"{dec_id} chưa ở trạng thái đã duyệt")
        else:
            w = risk_report_ok(root, dec)
            if w:
                why.append(f"{dec_id}: {w}")
            else:
                return dec_id, None
    return None, "; ".join(why)


def need_ticket(ctx, fp):
    """Cho qua chỉ khi có phiếu hợp lệ; ghi consumed.jsonl trước khi cho qua (ghi lỗi = deny)."""
    marker = "{{" + fp + "}}"
    if ctx["agent"] != MAIN:
        deny("chỉ phiên chính (Điều phối) được push/merge/ghi qua MCP, và phải có phiếu", ctx)
    root = ctx["root"]
    try:
        dec_id, why = find_ticket(root, fp)
    except Exception as e:
        deny(f"không kiểm được phiếu ({type(e).__name__}); từ chối để an toàn", ctx)
    if not dec_id:
        deny(f"cần phiếu approve_action kèm risk_report, câu hỏi bắt đầu bằng [DEC-xxxx] và chứa {marker}, "
             f"bạn bấm ô \"{YES}...\". Lý do: {why}", ctx)
    try:
        p = root / ".agents" / "ledger" / "consumed.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"dec": dec_id, "fingerprint": fp,
                                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                               ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        deny("không ghi được sổ phiếu đã dùng (ledger/consumed.jsonl); từ chối để an toàn", ctx)
    allow(ctx)


# ---------------------------------------------------------------- phân tích Bash
def find_heredoc_end(cmd, pos, delim):
    """Trả vị trí đầu dòng terminator (dòng đúng bằng delim) bắt đầu từ pos."""
    while True:
        nl = cmd.find("\n", pos)
        line = cmd[pos:] if nl < 0 else cmd[pos:nl]
        if line == delim:
            return pos
        if nl < 0:
            raise BashDeny("heredoc không có dòng kết thúc")
        pos = nl + 1


def scan(cmd):
    """Quét một lượt có nhận biết nháy: xử lý heredoc hợp lệ, đổi xuống dòng thành ';',
    chặn $( ` <( >( và mở rộng biến. Trả chuỗi đã chuẩn hoá."""
    out, seg, i, n, st = "", 0, 0, len(cmd), None
    while i < n:
        c = cmd[i]
        if st == "'":
            out += c
            i += 1
            if c == "'":
                st = None
            continue
        if c == "\\":
            if i + 1 >= n or cmd[i + 1] == "\n":
                raise BashDeny("dấu \\ cuối dòng/nối dòng không được hỗ trợ")
            out += cmd[i:i + 2]
            i += 2
            continue
        if c == "`":
            raise BashDeny("backtick (lệnh con) bị chặn")
        if c == "$":
            nx = cmd[i + 1:i + 2]
            if nx and ((st is None and nx in "'\"") or nx.isalnum() or nx in "_{(@*#?!$-"):
                raise BashDeny("mở rộng biến/lệnh con ($...) bị chặn")
            out += c
            i += 1
            continue
        if st == '"':
            out += c
            i += 1
            if c == '"':
                st = None
            continue
        # --- ngoài nháy
        if c == "'":
            st = "'"
        elif c == '"':
            m = HEREDOC_B.match(cmd, i)
            if m:  # mẫu: git commit -m "$(cat <<'EOF' ... EOF\n)"
                if not COMMIT_M.fullmatch(out[seg:].lstrip()):
                    raise BashDeny("heredoc chỉ được dùng trong git commit -m \"$(cat <<'EOF' ...)\"")
                pos = find_heredoc_end(cmd, m.end(), m.group(1))
                after = pos + len(m.group(1))
                if not cmd.startswith('\n)"', after):
                    raise BashDeny("heredoc -m có dạng lạ")
                out += '"HEREDOC"'
                i = after + 3
                continue
            st = '"'
        elif c in "<>" and cmd[i + 1:i + 2] == "(":
            raise BashDeny("process substitution <( >( bị chặn")
        elif c == "<" and cmd.startswith("<<", i):
            m = HEREDOC_A.match(cmd, i)  # mẫu: git commit -F - <<'EOF'
            if not m or not COMMIT_F.fullmatch(out[seg:].lstrip()):
                raise BashDeny("heredoc chỉ được dùng trong git commit -F - <<'EOF' (delimiter trong nháy)")
            pos = find_heredoc_end(cmd, m.end(), m.group(2))
            after = pos + len(m.group(2))
            if after < n and cmd[after] != "\n":
                raise BashDeny("heredoc có dạng lạ")
            i = after
            continue
        elif c == "\n":
            out += " ; "
            seg = len(out)
            i += 1
            continue
        elif c in ";&|":
            out += c
            i += 1
            seg = len(out)
            continue
        out += c
        i += 1
    if st is not None:
        raise BashDeny("dấu nháy không đóng")
    return out


def tokenize(text):
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        return list(lex)
    except ValueError as e:
        raise BashDeny(f"không phân tích được lệnh (shlex: {e})")


def strip_redirects(seg):
    """Chỉ cho >/dev/null, 2>/dev/null, 2>&1; mọi ký tự điều khiển khác còn sót = deny."""
    out, i = [], 0
    while i < len(seg):
        t = seg[i]
        if PUNCT.match(t):
            nxt = seg[i + 1] if i + 1 < len(seg) else None
            if t == ">" and nxt == "/dev/null":
                if out and out[-1] in ("1", "2"):
                    out.pop()
                i += 2
                continue
            if t == ">&" and nxt == "1" and out and out[-1] == "2":
                out.pop()
                i += 2
                continue
            raise BashDeny(f"chuyển hướng/cú pháp shell không được phép ('{t}'); chỉ cho >/dev/null, 2>/dev/null, 2>&1")
        out.append(t)
        i += 1
    return out


def arg_values(t):
    """Phần có thể là đường dẫn của một đối số (bỏ tên tuỳ chọn)."""
    if t.startswith("--"):
        return [t.split("=", 1)[1]] if "=" in t else []
    if t.startswith("-"):
        return [t[2:]] if len(t) > 2 else []
    return [t]


def check_args(args, ctx, skip=frozenset()):
    root, base = str(ctx["root"]), ctx["base"]
    for idx, t in enumerate(args):
        if idx in skip:
            continue
        for v in arg_values(t):
            if v == "/dev/null":
                continue
            if SECRET.search(v):
                raise BashDeny(f"đối số '{v}' trỏ tới file bí mật/khoá/biến môi trường")
            if re.search(r"[*?\[]", v):
                pat = v.split("/")[-1]
                for s in SECRET_SAMPLES:
                    if s.startswith(".") and not pat.startswith("."):
                        continue
                    if fnmatch.fnmatchcase(s, pat):
                        raise BashDeny(f"mẫu '{v}' có thể khớp file bí mật; hãy nêu tên file cụ thể")
            if re.search(r"\{[^{}]*(,|\.\.)[^{}]*\}", v):
                raise BashDeny("brace expansion {a,b} không được hỗ trợ")
            if v.startswith("~"):
                raise BashDeny("đường dẫn ~ ra ngoài dự án bị chặn")
            p = os.path.realpath(os.path.join(base, v))
            if not (p == root or p.startswith(root + os.sep)):
                raise BashDeny(f"đường dẫn '{v}' nằm ngoài thư mục dự án")


def abbrev(a, full, minlen=3):
    n = a.split("=", 1)[0]
    return len(n) >= minlen and full.startswith(n)


def check_git_read(rest, ctx):
    for a in rest:
        if a == "--":
            break
        if a.startswith("--") and (abbrev(a, "--output") or abbrev(a, "--ext-diff", 5)):
            raise BashDeny("git ... --output/--ext-diff bị chặn (ghi file/chạy chương trình ngoài)")
    check_args(rest, ctx)


def check_git_commit(rest, ctx):
    LONG_OK = {"--all", "--signoff", "--quiet", "--verbose", "--allow-empty", "--message", "--file",
               "--author", "--date", "--reset-author"}
    pending, i = None, 0
    for a in rest:
        if pending:
            if pending == "F" and a != "-":
                check_args([a], ctx)
            pending = None
            continue
        if a.startswith("--") and a != "--":
            if abbrev(a, "--amend") or abbrev(a, "--no-verify"):
                raise BashDeny("git commit --amend / --no-verify bị cấm")
            name, has_val = a.split("=", 1)[0], "=" in a
            if name not in LONG_OK:
                raise BashDeny(f"tuỳ chọn {name} của git commit không nằm trong danh sách cho phép")
            if name == "--message" and not has_val:
                pending = "m"
            elif name == "--file":
                if has_val:
                    if a.split("=", 1)[1] != "-":
                        check_args([a.split("=", 1)[1]], ctx)
                else:
                    pending = "F"
            continue
        if a.startswith("-") and len(a) > 1 and a != "--":
            chars = a[1:]
            for j, ch in enumerate(chars):
                if ch == "n":
                    raise BashDeny("git commit -n (= --no-verify) bị cấm")
                if ch in "mF":
                    val = chars[j + 1:]
                    if val:
                        if ch == "F" and val != "-":
                            check_args([val], ctx)
                    else:
                        pending = ch
                    break
                if ch not in "aqsv":
                    raise BashDeny(f"tuỳ chọn -{ch} của git commit không nằm trong danh sách cho phép")
            continue
        check_args([a], ctx)
    if pending:
        raise BashDeny("git commit thiếu giá trị cho -m/-F")


def check_git_ref(name, what):
    if not BRANCH.fullmatch(name) or ".." in name or name.endswith(("/", ".lock")):
        raise BashDeny(f"tên {what} '{name}' không hợp lệ")


def check_git(args, ctx):
    if not args:
        raise BashDeny("git không có lệnh con")
    sub, rest = args[0], args[1:]
    if sub.startswith("-"):
        raise BashDeny("git với tuỳ chọn toàn cục (-C, -c, --git-dir...) bị chặn")
    if sub in GIT_READ:
        check_git_read(rest, ctx)
    elif sub == "remote":
        if rest not in ([], ["-v"]):
            raise BashDeny("chỉ cho phép git remote hoặc git remote -v")
    elif sub == "branch":
        opts_ok = {"-a", "-r", "-v", "-vv", "--all", "--remotes", "--verbose", "--list", "--show-current"}
        val_opts = {"--contains", "--no-contains", "--merged", "--no-merged", "--points-at"}
        i = 0
        while i < len(rest):
            a = rest[i]
            if a in val_opts and i + 1 < len(rest):
                i += 2
                continue
            if a in opts_ok or (not a.startswith("-") and "--list" in rest):
                i += 1
                continue
            raise BashDeny("git branch chỉ được liệt kê (không tạo/xoá/đổi tên nhánh)")
    elif sub == "config":
        if not (len(rest) == 2 and rest[0] == "--get" and re.fullmatch(r"[\w.\-]+", rest[1])):
            raise BashDeny("chỉ cho phép git config --get <khoá>")
    elif sub == "add":
        for a in rest:
            if a.startswith("-") and a not in ("-A", "--all", "-u", "--update", "-v", "--verbose", "-n", "--dry-run", "--"):
                raise BashDeny(f"git add {a} không nằm trong danh sách cho phép")
        check_args(rest, ctx)
    elif sub == "commit":
        check_git_commit(rest, ctx)
    elif sub == "switch":
        ok = (len(rest) == 1 and (rest[0] == "main" or re.fullmatch(r"agent/REQ-\d{4}", rest[0]))) or \
             (len(rest) == 2 and rest[0] == "-c" and re.fullmatch(r"agent/REQ-\d{4}", rest[1]))
        if not ok:
            raise BashDeny("git switch chỉ tới main hoặc agent/REQ-xxxx (-c chỉ với agent/REQ-xxxx)")
    elif sub == "push":
        pos = [a for a in rest if not a.startswith("-")]
        if any(a.startswith("-") and a not in ("-u", "--set-upstream") for a in rest):
            raise BashDeny("git push chỉ cho phép -u; cấm --force, --delete, --mirror, --all, --tags...")
        if len(pos) != 2:
            raise BashDeny("git push phải nêu rõ <remote> <nhánh>")
        remote, ref = pos
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", remote):
            raise BashDeny("remote không hợp lệ (không nhận URL/đường dẫn)")
        if ":" in ref:
            a, b = ref.split(":", 1)
            if not a or a != b:
                raise BashDeny("refspec có ':' chỉ cho dạng nhánh:nhánh cùng tên")
            ref = a
        check_git_ref(ref, "nhánh")
        return f"PUSH {remote} {ref}"
    elif sub == "merge":
        pos = [a for a in rest if not a.startswith("-")]
        if any(a.startswith("-") and a not in ("--no-ff", "--ff-only", "--no-edit") for a in rest):
            raise BashDeny("git merge chỉ cho phép --no-ff, --ff-only, --no-edit")
        if len(pos) != 1:
            raise BashDeny("git merge phải nêu đúng một nhánh")
        check_git_ref(pos[0], "nhánh")
        cur = current_branch(ctx["root"])  # đọc .git/HEAD; detached/không đọc được -> deny
        if not cur or not BRANCH.fullmatch(cur):
            raise BashDeny("không xác định được nhánh hiện tại (.git/HEAD); git merge bị chặn")
        return f"MERGE {pos[0]} -> {cur}"
    else:
        raise BashDeny(f"git {sub} không nằm trong danh sách cho phép")
    return None


def check_pytest(args, ctx):
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-k", "-m"):
            if i + 1 >= len(args):
                raise BashDeny(f"pytest {a} thiếu giá trị")
            i += 2
            continue
        if PYTEST_OPT.fullmatch(a):
            i += 1
            continue
        if a.startswith("-"):
            raise BashDeny(f"tuỳ chọn pytest '{a}' không nằm trong danh sách cho phép")
        check_args([a], ctx)
        i += 1


def has_npmrc(ctx):
    """REQ-0004: có .npmrc ở gốc dự án, thư mục đang chạy, hoặc apps/*, packages/* -> True. Lỗi đọc = True (fail closed)."""
    try:
        root = ctx["root"]
        for d in (root, Path(ctx["base"])):
            if os.path.lexists(d / ".npmrc"):
                return True
        return any(True for pat in PKG_NPMRC_GLOBS for _ in root.glob(pat))
    except Exception:
        return True


def check_pkg(seg, ctx):
    """REQ-0004: npm/pnpm chỉ đúng argv trong PKG_ALLOW; chỉ Điều phối/reviewer; cấm khi có .npmrc."""
    if ctx["agent"] not in (MAIN, "reviewer"):
        raise BashDeny("agent này không được chạy npm/pnpm")
    if tuple(seg) not in PKG_ALLOW:
        raise BashDeny(f"'{' '.join(seg)}' không nằm trong danh sách npm/pnpm cố định "
                       "(không đối số thêm; cấm npx/node/yarn/install/ci/exec/dlx/run tuỳ ý)")
    if has_npmrc(ctx):
        raise BashDeny("repo có .npmrc (có thể đổi script-shell/registry); npm/pnpm bị chặn")
    ctx["pkg"] = True


def parse_opts(cmd, args):
    """Kiểm tuỳ chọn theo whitelist OPTS[cmd]. Trả (chỉ số đối số vị trí, chỉ số giá trị tuỳ chọn, tên tuỳ chọn đã dùng)."""
    short, shortval, long = OPTS[cmd]
    pos, vals, given = [], set(), set()
    i, end = 0, False
    while i < len(args):
        a = args[i]
        if end or a == "-" or not a.startswith("-"):
            pos.append(i)
            i += 1
            continue
        if a == "--":
            end = True
            i += 1
            continue
        if a.startswith("--"):
            name, has = a.split("=", 1)[0], "=" in a
            if name not in long:
                raise BashDeny(f"tuỳ chọn {name} của {cmd} không nằm trong whitelist")
            given.add(name)
            for _ in range(0 if has else long[name]):
                i += 1
                if i >= len(args):
                    raise BashDeny(f"{name} của {cmd} thiếu giá trị")
                vals.add(i)
            i += 1
            continue
        chars = a[1:]
        for j, ch in enumerate(chars):
            if ch in shortval:
                given.add("-" + ch)
                if not chars[j + 1:]:
                    i += 1
                    if i >= len(args):
                        raise BashDeny(f"-{ch} của {cmd} thiếu giá trị")
                    vals.add(i)
                break
            if ch not in short:
                raise BashDeny(f"tuỳ chọn -{ch} của {cmd} không nằm trong whitelist")
            given.add("-" + ch)
        i += 1
    return pos, vals, given


def check_find(args):
    if any(a in ("-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprint0",
                 "-fprintf", "-fls") for a in args):
        raise BashDeny("find -exec/-delete/-fprint... bị chặn")
    i = 0
    while i < len(args):
        a = args[i]
        if a in FIND_VAL:
            if i + 1 >= len(args):
                raise BashDeny(f"find {a} thiếu giá trị")
            i += 2
        elif a in FIND_FLAG:
            i += 1
        elif a.startswith("-"):
            raise BashDeny(f"find {a} không nằm trong whitelist")
        else:
            i += 1


def check_segment(seg, ctx):
    cmd, args = seg[0], seg[1:]
    if re.match(r"[A-Za-z_]\w*=", cmd):
        raise BashDeny("gán biến đầu lệnh (X=y lệnh) bị chặn")
    if cmd in BANNED:
        raise BashDeny(f"'{cmd}' bị chặn (chạy mã tuỳ ý / leo quyền / đọc môi trường)")
    if cmd in PKG_CMDS:
        check_pkg(seg, ctx)
        return None
    if "/" in cmd or cmd not in ALLOW:
        raise BashDeny(f"lệnh '{cmd}' không nằm trong danh sách cho phép")
    if cmd == "git":
        return check_git(args, ctx)
    if cmd == "pytest":
        check_pytest(args, ctx)
        return None
    if cmd == "python3":
        if len(args) >= 2 and args[0] == "-m" and args[1] == "pytest":
            check_pytest(args[2:], ctx)
        elif len(args) >= 2 and args[0] == "-m" and args[1] == "py_compile":
            check_args(args[2:], ctx)
        else:
            raise BashDeny("python3 chỉ cho phép -m pytest hoặc -m py_compile (cấm -c, -, chạy script)")
        return None
    skip = set()
    if cmd == "find":
        check_find(args)
    elif cmd in OPTS:
        pos, vals, given = parse_opts(cmd, args)
        if cmd == "pwd" and pos:
            raise BashDeny("pwd không nhận đối số")
        if cmd == "date" and any(not args[i].startswith("+") for i in pos):
            raise BashDeny("date chỉ nhận định dạng +FORMAT (đặt giờ hệ thống bị chặn)")
        if cmd == "uniq" and len(pos) > 1:
            raise BashDeny("uniq với file đầu ra bị chặn (ghi file)")
        if cmd == "jq" and any(re.search(r"\benv\b|\$ENV|\binput_filename\b", a) for a in args):
            raise BashDeny("jq đọc biến môi trường bị chặn")
        if cmd == "diff":
            for i in pos:  # diff thư mục đọc nội dung các file con
                if os.path.isdir(os.path.join(ctx["base"], args[i])):
                    raise BashDeny("diff thư mục bị chặn (đọc đệ quy); nêu từng file")
        if cmd in ("grep", "rg"):  # đối số mẫu tìm kiếm không phải đường dẫn
            have_e = "-e" in given or "--regexp" in given
            for i, a in enumerate(args):
                if a in ("-e", "--regexp"):
                    skip.add(i + 1)
            paths = pos if have_e else pos[1:]
            if not have_e and pos:
                skip.add(pos[0])
            if cmd == "rg":  # rg không có đường dẫn hoặc trỏ thư mục = đọc đệ quy
                if not paths:
                    raise BashDeny("rg phải nêu file cụ thể (không đọc đệ quy thư mục hiện tại)")
                for i in paths:
                    if os.path.isdir(os.path.join(ctx["base"], args[i])):
                        raise BashDeny("rg trên thư mục bị chặn (đọc đệ quy); nêu từng file")
    if cmd not in NO_PATH_CHECK:
        check_args(args, ctx, skip)
    return None


def check_bash(ctx, cmd):
    tickets = []
    try:
        toks = tokenize(scan(cmd))
        segs, any_seg, nseg = [[]], False, 0
        for t in toks:
            if t in (";", "&&", "||", "|"):
                segs.append([])
            else:
                segs[-1].append(t)
        for seg in segs:
            seg = strip_redirects(seg)
            if not seg:
                continue
            any_seg = True
            nseg += 1
            fp = check_segment(seg, ctx)
            if fp:
                tickets.append(fp)
        if not any_seg:
            raise BashDeny("lệnh rỗng")
        if ctx.get("pkg") and nseg > 1:
            raise BashDeny("npm/pnpm phải đứng một mình (không ghép ; && || | hay xuống dòng với lệnh khác)")
        if len(tickets) > 1:
            raise BashDeny("mỗi lệnh Bash chỉ được chứa một push/merge (mỗi hành động một phiếu)")
    except BashDeny as e:
        deny(f"lệnh bị chặn: {e}", ctx)
    if tickets:
        need_ticket(ctx, tickets[0])  # luôn thoát
    allow(ctx)


# ---------------------------------------------------------------- vân tay MCP, AskUserQuestion
def mcp_val(ctx, tin, key, what=None):
    v = tin.get(key)
    if not isinstance(v, str) or not MCP_VAL.fullmatch(v) or ".." in v:
        deny(f"tool MCP ghi thiếu/sai trường định danh '{key}'; từ chối để an toàn", ctx)
    return v


def mcp_fingerprint(ctx, name, tin, target):
    if name in MCP_PR or name in MCP_FILE or name in ("create_branch", "create_pull_request"):
        if target == "-":
            deny("tool MCP ghi thiếu owner/repo; từ chối để an toàn", ctx)
    if name in MCP_PR:
        n = tin.get("pullNumber")
        if isinstance(n, bool) or not (isinstance(n, int) and n > 0 or isinstance(n, str) and re.fullmatch(r"[1-9]\d*", n)):
            deny("tool MCP ghi thiếu pullNumber hợp lệ; từ chối để an toàn", ctx)
        return f"MCP {name} {target} #{int(n)}"
    if name in MCP_FILE:
        fp = f"MCP {name} {target} {mcp_val(ctx, tin, 'branch')}"
        if name == "push_files":
            files = tin.get("files")
            if isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict):
                fp += " " + mcp_val(ctx, files[0], "path")
        else:
            fp += " " + mcp_val(ctx, tin, "path")
        return fp
    if name == "create_branch":
        src = mcp_val(ctx, tin, "from_branch") if tin.get("from_branch") is not None else "-"
        return f"MCP create_branch {target} {src}->{mcp_val(ctx, tin, 'branch')}"
    if name == "create_pull_request":
        return f"MCP create_pull_request {target} {mcp_val(ctx, tin, 'head')}->{mcp_val(ctx, tin, 'base')}"
    fp = f"MCP {name} {target}"  # tool ghi khác/lạ: giữ vân tay owner/repo (thêm số PR/issue nếu có)
    for key in ("pullNumber", "issue_number"):
        v = tin.get(key)
        if isinstance(v, int) and not isinstance(v, bool):
            fp += f" #{v}"
    return fp


def nonempty(v):
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, (dict, list, tuple)):
        return len(v) > 0
    return True  # số, bool, kiểu lạ: coi là có nội dung (fail closed)


def check_ask(ctx, tin):
    """AskUserQuestion: chỉ kiểm cấu trúc. answers/annotations điền sẵn = câu trả lời không do người dùng bấm."""
    for key in ("answers", "annotations"):
        if nonempty(tin.get(key)):
            deny(f"AskUserQuestion có '{key}' điền sẵn; câu trả lời phải do người dùng bấm", ctx)
    allow(ctx)


# ---------------------------------------------------------------- main
def main():
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        out_deny("đầu vào hook không phải JSON hợp lệ; từ chối để an toàn")
    if not isinstance(data, dict):
        out_deny("đầu vào hook sai định dạng; từ chối để an toàn")
    tool = data.get("tool_name")
    tin = data.get("tool_input") or {}
    if not isinstance(tool, str) or not tool or not isinstance(tin, dict):
        out_deny("thiếu tool_name/tool_input hợp lệ; từ chối để an toàn")
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".").resolve()
    agent = data.get("agent_type")
    ctx = {"root": root, "agent": agent if isinstance(agent, str) and agent else MAIN,
           "tool": tool, "target": "", "base": str(root)}
    cwd = data.get("cwd")
    if isinstance(cwd, str) and cwd:
        b = os.path.realpath(cwd)
        if b == str(root) or b.startswith(str(root) + os.sep):
            ctx["base"] = b

    if tool == "AskUserQuestion":
        ctx["target"] = "AskUserQuestion"
        check_ask(ctx, tin)

    if tool in WRITE_TOOLS:
        raw = tin.get("file_path") or tin.get("notebook_path") or ""
        ctx["target"] = str(raw)
        p = (root / raw).resolve() if raw else None
        try:
            rel = p.relative_to(root).as_posix() if p else None
        except ValueError:
            rel = None
        check_write(ctx, rel)

    if tool == "Bash":
        cmd = tin.get("command", "")
        if not isinstance(cmd, str):
            deny("lệnh Bash không hợp lệ", ctx)
        ctx["target"] = cmd[:200]
        if ctx["agent"] not in (MAIN, "reviewer"):
            deny("agent này không được chạy lệnh shell", ctx)
        if len(str(tin.get("description") or "").strip()) < 10:
            deny("mỗi lệnh phải kèm mô tả (description) bằng tiếng Việt giải thích lệnh để làm gì", ctx)
        check_bash(ctx, cmd)

    if tool.startswith(MCP_PREFIX):
        name = tool[len(MCP_PREFIX):]
        ctx["target"] = tool
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            deny("tên tool MCP không hợp lệ", ctx)
        if name in MCP_READ:
            allow(ctx)
        owner, repo = tin.get("owner"), tin.get("repo")
        target = f"{owner}/{repo}" if isinstance(owner, str) and isinstance(repo, str) else "-"
        if target != "-" and not re.fullmatch(r"[\w.\-]+/[\w.\-]+", target):
            deny("owner/repo không hợp lệ", ctx)
        # tool ghi hoặc tên lạ: coi là ghi. Vân tay gồm định danh đối tượng; thiếu trường định danh = deny.
        need_ticket(ctx, mcp_fingerprint(ctx, name, tin, target))

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as e:  # fail closed
        out_deny(f"lỗi nội bộ của cổng ({type(e).__name__}); từ chối để an toàn")
