#!/usr/bin/env python3
"""Sổ câu trả lời gốc: hook PostToolUse cho AskUserQuestion.

Ghi nguyên văn câu hỏi + câu trả lời của người dùng vào .agents/ledger/answers.jsonl.
Không agent nào ghi được vào .agents/ledger/ (gate.py chặn), nên đây là bằng chứng
duy nhất để gate.py tin rằng một phiếu thật sự do người dùng duyệt.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    if data.get("tool_name") != "AskUserQuestion" or data.get("agent_type"):
        sys.exit(0)  # chỉ ghi khi Điều phối (phiên chính) hỏi người dùng
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".").resolve()
    p = root / ".agents" / "ledger" / "answers.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "questions": data.get("tool_input"),
            "response": data.get("tool_response"),
        }, ensure_ascii=False) + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
