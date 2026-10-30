#!/usr/bin/env python3
"""Cổng duyệt + Guard lớp 1: chặn bằng code mọi thao tác ghi/lệnh không được phép.

Chạy như hook PreToolUse của Claude Code. Đọc JSON từ stdin, in quyết định deny ra stdout.
Không in gì (exit 0) = để luồng quyền bình thường của Claude Code xử lý.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
MAIN = "orchestrator"  # phiên chính (không có agent_type)

# Guard lớp 1: lệnh có tác động ra ngoài hoặc phá huỷ (giai đoạn nháp: cấm hết)
BASH_DENY = [
    (r"\bgit\s+(push|merge|rebase|reset\s+--hard|clean|checkout\s+--|branch\s+-D)\b", "lệnh git thay đổi lịch sử/remote"),
    (r"\brm\s+-[a-zA-Z]*[rf]", "xoá đệ quy/cưỡng bức"),
    (r"\b(curl|wget|ssh|scp|rsync|nc|telnet)\b", "gọi mạng ra ngoài"),
    (r"\b(npm|pnpm|yarn)\s+publish\b|\bdocker\s+push\b|\bkubectl\b|\bterraform\s+apply\b|\bdeploy\b", "deploy/xuất bản"),
    (r"\bsed\s+-i\b|\btee\b|\bmv\b|\bcp\b|\bchmod\b|\bchown\b|\btruncate\b", "ghi file bằng shell (phải dùng Write/Edit để qua cổng)"),
    (r"(^|[^0-9&])>{1,2}\s*(?!/dev/null|&)", "chuyển hướng ghi file bằng shell"),
    (r"\b(printenv|env)\b|\.env\b|id_rsa|\.pem\b", "đọc biến môi trường/khoá bí mật"),
]


def deny(reason, ctx):
    log(ctx, "deny", reason)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"[gate] {reason}",
    }}, ensure_ascii=False))
    sys.exit(0)


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
    except OSError:
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


def executor_approved(root, rel):
    """Thực thi chỉ được ghi trong drafts/<REQ>/ khi task đang chạy mang mã quyết định đã duyệt."""
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
    req = active.get("request_id", "")
    if not re.fullmatch(r"REQ-\d{4}", req) or not rel.startswith(f"drafts/{req}/"):
        return f"chỉ được ghi trong drafts/{req}/"
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
    if rel.startswith("drafts/"):
        if agent != "executor":
            deny("chỉ Thực thi được ghi bản nháp", ctx)
        why = executor_approved(root, rel)
        if why:
            deny(why, ctx)
        allow(ctx)
    deny("giai đoạn nháp: chỉ được ghi trong .agents/ và drafts/", ctx)


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".").resolve()
    tool = data.get("tool_name", "")
    tin = data.get("tool_input") or {}
    ctx = {"root": root, "agent": data.get("agent_type") or MAIN, "tool": tool, "target": ""}

    if tool in WRITE_TOOLS:
        raw = tin.get("file_path") or tin.get("notebook_path") or ""
        ctx["target"] = raw
        p = (root / raw).resolve() if raw else None
        try:
            rel = p.relative_to(root).as_posix() if p else None
        except ValueError:
            rel = None
        check_write(ctx, rel)

    if tool == "Bash":
        cmd = tin.get("command", "")
        ctx["target"] = cmd[:200]
        if ctx["agent"] not in (MAIN, "reviewer"):
            deny("agent này không được chạy lệnh shell", ctx)
        if len((tin.get("description") or "").strip()) < 10:
            deny("mỗi lệnh phải kèm mô tả (description) bằng tiếng Việt giải thích lệnh để làm gì", ctx)
        for pat, why in BASH_DENY:
            if re.search(pat, cmd):
                deny(f"lệnh bị chặn ({why}); cần người dùng tự chạy hoặc mở ở giai đoạn sau", ctx)
        allow(ctx)

    sys.exit(0)


if __name__ == "__main__":
    main()
