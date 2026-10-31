"""Kiểm thử gate.py (REQ-0002; mở rộng REQ-0004: allowlist npm/pnpm cố định, .npmrc, .gitignore).
Chạy gate qua subprocess với JSON PreToolUse giả lập.

- GATE_PATH (env) chọn bản gate cần kiểm; mặc định: ../.claude/hooks/gate.py cạnh thư mục tests/
  (khi đã áp vào dự án thì chính là .claude/hooks/gate.py).
- CLAUDE_PROJECT_DIR luôn là thư mục tạm; không đụng .agents/ thật, không dùng mạng.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GATE = os.environ.get("GATE_PATH") or str(Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "gate.py")
DESC = "Mô tả lệnh bằng tiếng Việt để kiểm thử"
REQ = "REQ-0002"
PUSH = "git push origin agent/REQ-0002"
PUSH_FP = "PUSH origin agent/REQ-0002"
MERGE = "git merge agent/REQ-0002"
MERGE_FP = "MERGE agent/REQ-0002 -> main"  # fixture proj: .git/HEAD = main


@pytest.fixture
def proj(tmp_path):
    for d in (".agents/decisions", ".agents/artifacts", ".agents/state", ".agents/ledger", ".git"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("git push\n", encoding="utf-8")
    return tmp_path


def run_raw(proj, stdin_text):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(proj))
    env.pop("GATE_PATH", None)
    r = subprocess.run([sys.executable, GATE], input=stdin_text, capture_output=True, text=True,
                       env=env, cwd=str(proj), timeout=30)
    return r


def run(proj, tool, tin, agent=None):
    data = {"tool_name": tool, "tool_input": tin, "cwd": str(proj)}
    if agent:
        data["agent_type"] = agent
    return run_raw(proj, json.dumps(data))


def decision(r):
    """Trả (deny?, lý do). Allow = stdout rỗng và exit 0."""
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return False, ""
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    return True, out["permissionDecisionReason"]


def bash(proj, cmd, agent=None, desc=DESC):
    tin = {"command": cmd}
    if desc is not None:
        tin["description"] = desc
    return decision(run(proj, "Bash", tin, agent))


def write(proj, path, agent=None):
    return decision(run(proj, "Write", {"file_path": str(proj / path), "content": "x"}, agent))


def mcp(proj, name, **tin):
    return decision(run(proj, "mcp__github__" + name, tin))


def jwrite(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def ledger_add(proj, question, label):
    rec = {"at": "2026-10-30T00:00:00+00:00",
           "questions": {"questions": [{"question": question}]},
           "response": {"answers": {question: label}}}
    with (proj / ".agents" / "ledger" / "answers.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def make_ticket(proj, fp, dec="DEC-0100", label="Đồng ý (Khuyến nghị)", kind="approve_action",
                status="answered", action="choose", risk=True, ledger=True, art_type="risk_report",
                created_by="guard", art_req=REQ, question=None):
    d = {"id": dec, "request_id": REQ, "kind": kind, "status": status,
         "answer": {"action": action}}
    if risk:
        d["risk_report"] = "ART-0900"
        jwrite(proj / ".agents/artifacts/ART-0900.json",
               {"id": "ART-0900", "request_id": art_req, "type": art_type, "created_by": created_by})
    jwrite(proj / f".agents/decisions/{dec}.json", d)
    if ledger:
        ledger_add(proj, question or f"[{dec}] Cho phép? {{{{{fp}}}}}", label)


def consumed(proj):
    p = proj / ".agents/ledger/consumed.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def setup_executor(proj, risk=True, branch=True):
    if branch:
        (proj / ".agents/workflow.yaml").write_text("steps:\n  execution:\n    mode: branch\n", encoding="utf-8")
        (proj / ".git/HEAD").write_text(f"ref: refs/heads/agent/{REQ}\n", encoding="utf-8")
    make_ticket(proj, "x", dec="DEC-0200", risk=risk, question="[DEC-0200] Cho executor làm?")
    jwrite(proj / ".agents/state/active_task.json",
           {"agent": "executor", "request_id": REQ, "approved_by_decision": "DEC-0200"})


# ---------------------------------------------------------------- AC1, AC1b
@pytest.mark.parametrize("cmd", [
    "python3 -c \"open('.agents/decisions/DEC-9999.json','w').write('x')\"",
    "python3 - <<'EOF'\nopen('.agents/decisions/DEC-9999.json','w').write('x')\nEOF",
])
def test_ac1_python_ghi_bi_chan(proj, cmd):
    assert bash(proj, cmd)[0]


@pytest.mark.parametrize("cmd", [
    "echo x > .agents/state/a",
    "tee .claude/x",
    "sed -i s/a/b/ CLAUDE.md",
    "cp a .agents/b",
    "mv a .claude/b",
])
def test_ac1b_ghi_bang_shell_bi_chan(proj, cmd):
    assert bash(proj, cmd)[0]


# ---------------------------------------------------------------- AC2, AC3, AC4, AC4b
@pytest.mark.parametrize("cmd", ['echo "git push"', 'grep -n "git push" CLAUDE.md'])
def test_ac2_chuoi_git_push_trong_doi_so_khong_bi_chan(proj, cmd):
    assert bash(proj, cmd) == (False, "")


def test_ac3_merge_base_cho_qua(proj):
    assert bash(proj, "git merge-base main HEAD") == (False, "")


def test_ac4_commit_heredoc_F(proj):
    cmd = "git commit -F - <<'EOF'\nfix deploy note > x\n\nCo-Authored-By: A <a@b.c>\nEOF"
    assert bash(proj, cmd) == (False, "")


def test_ac4_commit_heredoc_m(proj):
    cmd = "git commit -m \"$(cat <<'EOF'\nfix deploy note\n\nCo-Authored-By: A <a@b.c>\nEOF\n)\""
    assert bash(proj, cmd) == (False, "")


def test_ac4_commit_m_thuong(proj):
    assert bash(proj, 'git commit -m "fix deploy note; a && b > c"') == (False, "")


@pytest.mark.parametrize("cmd", ["deploy", "./deploy.sh", "deploy --prod"])
def test_ac4b_deploy_la_lenh_thuc_thi_bi_chan(proj, cmd):
    assert bash(proj, cmd)[0]


# ---------------------------------------------------------------- AC5, AC5b (push)
def test_ac5_push_khong_phieu_bi_chan(proj):
    assert bash(proj, PUSH)[0]


def test_ac5_push_co_phieu_hop_le_cho_qua_va_ghi_so(proj):
    make_ticket(proj, PUSH_FP)
    assert bash(proj, PUSH) == (False, "")
    rec = consumed(proj)
    assert len(rec) == 1 and rec[0]["dec"] == "DEC-0100" and rec[0]["fingerprint"] == PUSH_FP


@pytest.mark.parametrize("kw", [
    {"status": "open"},
    {"action": "reject"},
    {"ledger": False},
    {"risk": False},
    {"art_type": "plan"},
    {"created_by": "executor"},
    {"art_req": "REQ-0003"},
    {"kind": "approve_stage"},
    {"label": "Không đồng ý"},
    {"label": "Từ chối"},
    {"fp": "PUSH origin main"},          # phiếu cho nhánh khác
    {"fp": "MERGE agent/REQ-0002 -> main"},      # phiếu cho lệnh khác
])
def test_ac5b_phieu_khong_hop_le_bi_chan(proj, kw):
    kw = dict(kw)
    fp = kw.pop("fp", PUSH_FP)
    make_ticket(proj, fp, **kw)
    assert bash(proj, PUSH)[0]
    assert consumed(proj) == []


def test_ac5b_phieu_dung_lan_hai_bi_chan(proj):
    make_ticket(proj, PUSH_FP)
    assert bash(proj, PUSH) == (False, "")
    assert bash(proj, PUSH)[0]


def test_ac5b_dec_tu_ghi_san_answered_khong_co_ledger_bi_chan(proj):
    jwrite(proj / ".agents/decisions/DEC-0100.json",
           {"id": "DEC-0100", "request_id": REQ, "kind": "approve_action", "status": "answered",
            "answer": {"action": "choose"}, "risk_report": "ART-0900"})
    jwrite(proj / ".agents/state/active_task.json", {"agent": "orchestrator", "approved_by_decision": "DEC-0100"})
    assert bash(proj, PUSH)[0]


def test_ac5b_ghi_so_loi_thi_chan(proj):
    make_ticket(proj, PUSH_FP)
    (proj / ".agents/ledger/consumed.jsonl").mkdir()  # ghi/đọc file này sẽ lỗi
    assert bash(proj, PUSH)[0]


@pytest.mark.parametrize("cmd", [
    "git push --force origin agent/REQ-0002",
    "git push origin",
    "git push origin :agent/REQ-0002",
    "git push origin main:other",
    "git push https://x.invalid/r.git agent/REQ-0002",
])
def test_ac5_push_dang_nguy_hiem_bi_chan_du_co_phieu(proj, cmd):
    make_ticket(proj, PUSH_FP)
    assert bash(proj, cmd)[0]


# ---------------------------------------------------------------- AC6 (merge)
def test_ac6_merge_khong_phieu_bi_chan(proj):
    assert bash(proj, MERGE)[0]


def test_ac6_merge_co_phieu_cho_qua_mot_lan(proj):
    make_ticket(proj, MERGE_FP, dec="DEC-0101")
    assert bash(proj, MERGE) == (False, "")
    assert bash(proj, MERGE)[0]


def test_ac6_phieu_push_khong_dung_cho_merge(proj):
    make_ticket(proj, PUSH_FP)
    assert bash(proj, MERGE)[0]


def test_ac6_merge_force_bi_chan(proj):
    make_ticket(proj, MERGE_FP, dec="DEC-0101")
    assert bash(proj, "git merge --abort")[0]
    assert bash(proj, "git merge -X theirs agent/REQ-0002")[0]


def test_ac6_hai_hanh_dong_trong_mot_lenh_bi_chan(proj):
    make_ticket(proj, PUSH_FP)
    make_ticket(proj, MERGE_FP, dec="DEC-0101")
    assert bash(proj, PUSH + " && " + MERGE)[0]
    assert consumed(proj) == []


def test_reviewer_khong_duoc_push(proj):
    make_ticket(proj, PUSH_FP)
    assert bash(proj, PUSH, agent="reviewer")[0]


# ---------------------------------------------------------------- AC7 (MCP)
@pytest.mark.parametrize("name", ["create_pull_request", "push_files", "merge_pull_request",
                                  "create_branch", "create_or_update_file", "update_pull_request",
                                  "delete_file", "issue_write", "add_issue_comment", "tool_la_moi_xuat_hien"])
def test_ac7_mcp_ghi_khong_phieu_bi_chan(proj, name):
    assert mcp(proj, name, owner="o", repo="r")[0]


PR = dict(owner="o", repo="r", head="agent/REQ-0002", base="main")
PR_FP = "MCP create_pull_request o/r agent/REQ-0002->main"


def test_ac7_mcp_ghi_co_phieu_cho_qua_mot_lan(proj):
    make_ticket(proj, PR_FP, dec="DEC-0102")
    assert mcp(proj, "create_pull_request", **PR) == (False, "")
    assert mcp(proj, "create_pull_request", **PR)[0]


def test_ac7_phieu_mcp_sai_repo_hoac_sai_tool(proj):
    make_ticket(proj, PR_FP, dec="DEC-0102")
    assert mcp(proj, "create_pull_request", **dict(PR, repo="khac"))[0]
    assert mcp(proj, "push_files", owner="o", repo="r", branch="agent/REQ-0002")[0]


# ---------------------------------------------------------------- L4 (vân tay MCP/MERGE)
def test_l4_create_pull_request_sai_head_base_bi_chan(proj):
    make_ticket(proj, PR_FP, dec="DEC-0102")
    assert mcp(proj, "create_pull_request", **dict(PR, base="release"))[0]
    assert mcp(proj, "create_pull_request", **dict(PR, head="khac"))[0]
    assert consumed(proj) == []


def test_l4_merge_pr_theo_so_pr(proj):
    make_ticket(proj, "MCP merge_pull_request o/r #5", dec="DEC-0103")
    assert mcp(proj, "merge_pull_request", owner="o", repo="r", pullNumber=6)[0]
    assert mcp(proj, "merge_pull_request", owner="o", repo="r", pullNumber=5) == (False, "")
    assert mcp(proj, "merge_pull_request", owner="o", repo="r", pullNumber=5)[0]


@pytest.mark.parametrize("name", sorted(["merge_pull_request", "update_pull_request"]))
def test_l4_thieu_pullnumber_bi_chan(proj, name):
    make_ticket(proj, f"MCP {name} o/r", dec="DEC-0103")
    r = run(proj, "mcp__github__" + name, {"owner": "o", "repo": "r"})
    assert decision(r)[0]
    assert consumed(proj) == []


def test_l4_push_files_va_file_don(proj):
    make_ticket(proj, "MCP push_files o/r agent/REQ-0002", dec="DEC-0104")
    assert mcp(proj, "push_files", owner="o", repo="r", branch="main")[0]
    assert mcp(proj, "push_files", owner="o", repo="r")[0]  # thiếu branch
    assert mcp(proj, "push_files", owner="o", repo="r", branch="agent/REQ-0002",
               files=[{"path": "a", "content": "x"}])[0]  # một file: vân tay có path, phiếu không khớp
    assert mcp(proj, "push_files", owner="o", repo="r", branch="agent/REQ-0002",
               files=[{"path": "a"}, {"path": "b"}]) == (False, "")  # nhiều file: chỉ branch
    make_ticket(proj, "MCP create_or_update_file o/r agent/REQ-0002 src/a.py", dec="DEC-0105")
    assert mcp(proj, "create_or_update_file", owner="o", repo="r", branch="agent/REQ-0002", path="src/b.py")[0]
    assert mcp(proj, "create_or_update_file", owner="o", repo="r", branch="agent/REQ-0002",
               path="src/a.py") == (False, "")
    assert mcp(proj, "delete_file", owner="o", repo="r", branch="agent/REQ-0002")[0]  # thiếu path


def test_l4_create_branch(proj):
    make_ticket(proj, "MCP create_branch o/r main->agent/REQ-0002", dec="DEC-0106")
    assert mcp(proj, "create_branch", owner="o", repo="r", branch="agent/REQ-0002", from_branch="dev")[0]
    assert mcp(proj, "create_branch", owner="o", repo="r", from_branch="main")[0]  # thiếu branch
    assert mcp(proj, "create_branch", owner="o", repo="r", branch="agent/REQ-0002",
               from_branch="main") == (False, "")


def test_l4_phieu_vet_tho_cu_khong_con_khop(proj):
    make_ticket(proj, "MCP create_pull_request o/r", dec="DEC-0102")
    assert mcp(proj, "create_pull_request", **PR)[0]


def test_l4_merge_vet_tay_gom_nhanh_hien_tai(proj):
    make_ticket(proj, "MERGE agent/REQ-0002", dec="DEC-0101")  # vân tay cũ
    assert bash(proj, MERGE)[0]
    (proj / ".git/HEAD").write_text("ref: refs/heads/release\n", encoding="utf-8")
    make_ticket(proj, MERGE_FP, dec="DEC-0107")  # phiếu cho main, đang ở release
    assert bash(proj, MERGE)[0]
    (proj / ".git/HEAD").write_text("0123456789abcdef\n", encoding="utf-8")  # detached
    assert bash(proj, MERGE)[0]


# ---------------------------------------------------------------- L5 (AskUserQuestion)
QS = [{"question": "[DEC-0001] Chọn?", "header": "h", "multiSelect": False,
       "options": [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]}]


@pytest.mark.parametrize("extra", [
    {"answers": {"[DEC-0001] Chọn?": "Đồng ý"}},
    {"annotations": {"[DEC-0001] Chọn?": {"notes": "x"}}},
    {"answers": ["x"]},
    {"answers": "x"},
    {"answers": 1},
])
def test_l5_answers_dien_san_bi_chan(proj, extra):
    assert decision(run(proj, "AskUserQuestion", dict({"questions": QS}, **extra)))[0]


@pytest.mark.parametrize("extra", [{}, {"answers": {}}, {"annotations": {}}, {"answers": ""}, {"answers": None},
                                   {"answers": [], "annotations": {}}])
def test_l5_khong_co_hoac_rong_cho_qua(proj, extra):
    assert decision(run(proj, "AskUserQuestion", dict({"questions": QS}, **extra))) == (False, "")


def test_l5_json_hong_van_deny(proj):
    assert decision(run_raw(proj, "{khong phai json"))[0]


@pytest.mark.parametrize("name", ["get_file_contents", "list_issues", "search_code", "issue_read",
                                  "pull_request_read", "get_me", "list_branches"])
def test_ac7_mcp_doc_cho_qua(proj, name):
    assert mcp(proj, name, owner="o", repo="r") == (False, "")


# ---------------------------------------------------------------- AC8 (executor)
def test_ac8_executor_ghi_vung_cam_bi_chan(proj):
    setup_executor(proj)
    for path in (".agents/x", ".claude/x", "CLAUDE.md", ".github/x", ".agents/ledger/consumed.jsonl"):
        assert write(proj, path, "executor")[0], path


@pytest.mark.parametrize("cmd", ["echo x > .agents/x", "tee .claude/x", "cp a .github/x", "sed -i s/a/b/ CLAUDE.md"])
def test_ac8_executor_bash_bi_chan(proj, cmd):
    setup_executor(proj)
    assert bash(proj, cmd, "executor")[0]


def test_ac8_executor_ghi_code_hop_le(proj):
    setup_executor(proj)
    assert write(proj, "src/x.py", "executor") == (False, "")


def test_ac8_executor_sai_nhanh_bi_chan(proj):
    setup_executor(proj)
    (proj / ".git/HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    assert write(proj, "src/x.py", "executor")[0]


def test_ac8_moi_agent_khong_sua_claude_hoac_ledger(proj):
    for agent in (None, "executor", "designer"):
        assert write(proj, ".claude/hooks/gate.py", agent)[0]
        assert write(proj, "CLAUDE.md", agent)[0]
        assert write(proj, ".agents/ledger/answers.jsonl", agent)[0]


# ---------------------------------------------------------------- AC9, AC9b
@pytest.mark.parametrize("cmd", ["ls /", "make", "curl http://x"])
def test_ac9_deny_co_ly_do_tieng_viet(proj, cmd):
    denied, why = bash(proj, cmd)
    assert denied and any(ord(c) > 127 for c in why)


@pytest.mark.parametrize("cmd", ["git status", "git diff", "git log", "pytest", "ls", "cat CLAUDE.md",
                                 "git add -A && git status", "git switch agent/REQ-0002",
                                 "ls 2>/dev/null", "git status 2>&1", "python3 -m pytest -q tests"])
def test_ac9b_trong_allowlist_cho_qua(proj, cmd):
    assert bash(proj, cmd) == (False, "")


@pytest.mark.parametrize("cmd", [
    "sort -u a", "sort -nr -k 2 a", "sort -t , -k2 a", "date", "date -u", "date +%Y-%m-%d", "file a", "file -b a",
    "uniq -c a", "head -n 5 CLAUDE.md", "head -5 CLAUDE.md", "tail -n5 CLAUDE.md", "wc -l CLAUDE.md",
    "ls -la", "ls -1 --all", "cat -n CLAUDE.md", "grep -n git CLAUDE.md", "grep -in -A2 git CLAUDE.md",
    "grep -e git CLAUDE.md", "grep --max-count=1 git CLAUDE.md", "rg -n git CLAUDE.md",
    "rg -i -e git CLAUDE.md", "diff -u CLAUDE.md CLAUDE.md", "find . -name '*.py' -type f",
    "find . -maxdepth 2 -type d", "jq -r .a CLAUDE.md", "cut -d , -f 1 CLAUDE.md",
    "stat -c %s CLAUDE.md", "git diff --stat", "pwd",
])
def test_l1_tuy_chon_whitelist_cho_qua(proj, cmd):
    assert bash(proj, cmd) == (False, ""), cmd


def test_ac9b_thieu_description_bi_chan(proj):
    assert bash(proj, "git status", desc=None)[0]
    assert bash(proj, "git status", desc="ngắn")[0]


# ---------------------------------------------------------------- AC12
def test_ac12_thuc_thi_thieu_risk_report_bi_chan(proj):
    setup_executor(proj, risk=False)
    assert write(proj, "src/x.py", "executor")[0]


def test_ac12_thuc_thi_che_do_nhap(proj):
    setup_executor(proj, branch=False)
    assert write(proj, f"drafts/{REQ}/a.md", "executor") == (False, "")
    assert write(proj, "src/a.py", "executor")[0]


def test_ac12_phieu_sai_loai_bi_chan(proj):
    setup_executor(proj)
    d = json.loads((proj / ".agents/decisions/DEC-0200.json").read_text(encoding="utf-8"))
    d["kind"] = "approve_stage"
    jwrite(proj / ".agents/decisions/DEC-0200.json", d)
    assert write(proj, "src/x.py", "executor")[0]


# ---------------------------------------------------------------- ca lách
@pytest.mark.parametrize("cmd", [
    'bash -c "git push origin agent/REQ-0002"',
    "sh -c 'rm -rf x'",
    "echo $(git push origin agent/REQ-0002)",
    "echo `whoami`",
    "eval 'ls'",
    "exec ls",
    "source ./x.sh",
    ". ./x.sh",
    "git -C /tmp status",
    "git -c core.pager=x status",
    "python3 -c 'print(1)'",
    "python3 script.py",
    "X=1 ls",
    "sudo ls",
    "ls | xargs rm",
    "find . -exec rm {} ;",
    "find . -delete",
    "cat <(ls)",
    "ls; rm -rf x",
    "ls && curl http://x",
    "ls || wget http://x",
    "cat .env",
    "cat ./config/id_rsa",
    "cat .e*",
    "cat /etc/passwd",
    "cat ../outside.txt",
    "cat ~/.bashrc",
    "ls /",
    "printenv",
    "env",
    "echo hi > out.txt",
    "echo hi >> CLAUDE.md",
    "cat < CLAUDE.md",
    "ls &",
    "git commit --amend -m x",
    "git commit --no-verify -m x",
    "git commit -nm x",
    "git commit --am -m x",
    "git reset --hard",
    "git checkout -- .",
    "git branch -D x",
    "git branch newbranch",
    "git switch some-other",
    "git config user.name x",
    "git diff --output=x.txt",
    "sort -o out.txt a",
    "rg --pre ./x foo",
    "sort --o=f a", "sort --outp=f a", "sort --out=f a", "sort --com=x a", "sort --compress-program=x a",
    "sort -T /tmp a", "sort -uo f a", "date --se=x", "date --set=x", "date -s x", "date 010112002026",
    "file --comp a", "file -C", "file --compile",
    "uniq a b", "head --foo a", "cat --foo a", "ls --color=always -R", "ls -R",
    "grep -r KEY .", "grep -R KEY .", "grep -rn KEY .", "grep -Rl KEY .", "grep --recursive KEY .",
    "grep --rec KEY .", "grep --dereference-recursive KEY .", "grep -nr KEY .", "grep -d recurse KEY .",
    "rg -u KEY .", "rg -uu KEY a", "rg --hidden KEY a", "rg --no-ignore KEY a", "rg --follow KEY a",
    "rg KEY", "rg KEY .", "rg KEY CLAUDE.md .", "rg -L KEY a", "rg -r x KEY a",
    "diff -r a b", "diff -qr a b", "diff --recursive a b", "diff .agents .claude",
    "find . -L", "find . -fprint x", "find . -regex x", "find . -newerXY x", "find -H .",
    "jq --rawfile x a .", "jq --slurpfile x a .", "jq -f prog a", "jq -L . x", "jq --arg a",
    "stat --foo a", "wc --files0-from=x", "tail -f a", "tail --follow a", "cut --foo a",
    "jq -n env",
    "pytest -p evil",
    "pytest --junitxml=.claude/x.xml",
    "echo 'unterminated",
    "echo $HOME",
    "echo ${IFS}",
    "ls ls\\\nrm",
])
def test_lach_bi_chan(proj, cmd):
    assert bash(proj, cmd)[0], cmd


def test_lach_heredoc_giau_lenh_trong_nhay(proj):
    # Heredoc giả nằm trong chuỗi nháy để che "git push" thật: phải bị phát hiện
    cmd = 'echo "\nx <<\'EOF\'\n"; git push origin agent/REQ-0002; echo "\nEOF\n"'
    assert bash(proj, cmd)[0]


@pytest.mark.parametrize("cmd", [
    "cat <<EOF\nx\nEOF",
    "cat <<'EOF'\nx\nEOF",
    "git commit -F - <<'EOF'\nx\nEOF2",       # không có dòng kết thúc
    "git commit -F - <<'EOF' && rm x\nx\nEOF",  # còn lệnh sau marker
    "git commit -m \"$(cat <<'EOF'\nx\nEOF\n) && rm\"",
    "ls <<< hi",
])
def test_lach_heredoc_la_bi_chan(proj, cmd):
    assert bash(proj, cmd)[0], cmd


def test_lach_heredoc_commit_nhung_them_lenh_sau(proj):
    cmd = "git commit -F - <<'EOF'\nmsg\nEOF\nrm -rf x"
    assert bash(proj, cmd)[0]


# ---------------------------------------------------------------- fail closed
@pytest.mark.parametrize("stdin", ["", "không phải json", "{", "[]", "null", '{"tool_name": ""}',
                                   '{"tool_input": {}}', '{"tool_name": "Bash", "tool_input": "x"}',
                                   '{"tool_name": 5}'])
def test_fail_closed_json_hong(proj, stdin):
    denied, why = decision(run_raw(proj, stdin))
    assert denied and why


def test_fail_closed_lenh_khong_phai_chuoi(proj):
    assert decision(run(proj, "Bash", {"command": ["ls"], "description": DESC}))[0]


def test_fail_closed_ngoai_le_noi_bo(proj):
    # file_path chứa byte NUL làm Path.resolve() lỗi -> phải deny, không crash/cho qua
    denied, _ = decision(run(proj, "Write", {"file_path": "a\x00b"}))
    assert denied


def test_tool_khac_khong_bi_chan(proj):
    assert decision(run(proj, "Read", {"file_path": "x"})) == (False, "")


# ================================================================ REQ-0004
# ---------------------------------------------------------------- npm/pnpm: allowlist cố định
PKG_OK = ["pnpm test", "pnpm lint", "pnpm typecheck", "pnpm build",
          "npm test", "npm run lint", "npm run typecheck", "npm run build", "npm run build:web"]


@pytest.mark.parametrize("agent", [None, "reviewer"])
@pytest.mark.parametrize("cmd", PKG_OK)
def test_pkg_lenh_co_dinh_cho_qua(proj, cmd, agent):
    assert bash(proj, cmd, agent) == (False, ""), cmd


def test_pkg_cho_qua_voi_redirect_dev_null(proj):
    assert bash(proj, "pnpm test 2>&1") == (False, "")
    assert bash(proj, "npm test >/dev/null") == (False, "")


@pytest.mark.parametrize("cmd", [
    # đối số thêm / viết tắt / tên script khác
    "pnpm test -- x", "npm test -- --x", "pnpm test --filter a", "pnpm test -r", "pnpm -r test",
    "pnpm --filter a test", "pnpm -w test", "npm test -w a", "npm test --workspaces",
    "npm run build:web -w a", "npm run lint --prefix x", "npm --prefix x test",
    "npm t", "npm tst", "npm run dev", "npm run start", "npm run test", "pnpm run test", "pnpm run lint",
    "pnpm build:web", "pnpm dev", "pnpm start", "pnpm", "npm", "npm run", "npm run build:web x",
    "pnpm test x", "npm test x",
    # lệnh khác họ npm
    "npx x", "npx pnpm test", "node x.js", "yarn test", "corepack enable", "corepack pnpm test",
    # cài đặt / thực thi gói
    "pnpm install", "pnpm i", "pnpm add x", "pnpm dlx x", "pnpm exec x", "pnpm x x", "pnpm rebuild",
    "npm install", "npm i", "npm ci", "npm exec x", "npm x x", "npm install-test", "npm rebuild",
    # đường dẫn, biến môi trường đầu lệnh, lệnh bọc
    "./node_modules/.bin/pnpm test", "/usr/bin/npm test", "FOO=1 npm test",
    "NPM_CONFIG_SCRIPT_SHELL=x pnpm test", "env npm test", "env FOO=1 pnpm test", "sudo npm test",
    "time npm test", "timeout 5 npm test", "xargs npm test", "bash -c 'npm test'",
])
def test_pkg_lach_bi_chan(proj, cmd):
    assert bash(proj, cmd)[0], cmd


@pytest.mark.parametrize("cmd", [
    "npm test; ls", "pnpm test; ls", "npm test && ls", "npm test || ls", "ls && npm test", "ls; pnpm test",
    "npm test | cat", "pnpm test | sh", "npm test & ", "npm test\nls", "pnpm test\nrm -rf x",
    "npm test\nnpm test", "npm test; npm test", "pnpm test && curl http://x",
    "pnpm test $(ls)", "pnpm test `ls`", "pnpm test $HOME", "pnpm test ${IFS}x", "echo $(npm test)",
    "pnpm test > out.txt", "pnpm test >> CLAUDE.md", "pnpm test < CLAUDE.md", "npm test <(ls)",
    "pnpm test \\\nx",
])
def test_pkg_ghep_lenh_bi_chan(proj, cmd):
    assert bash(proj, cmd)[0], cmd


@pytest.mark.parametrize("agent", ["executor", "analyst", "designer", "planner", "guard", "reporter", "intake"])
def test_pkg_chi_dieu_phoi_va_reviewer(proj, agent):
    assert bash(proj, "pnpm test", agent)[0]
    assert bash(proj, "npm test", agent)[0]


def test_pkg_thieu_description_bi_chan(proj):
    assert bash(proj, "pnpm test", desc=None)[0]


def test_pkg_deny_co_ly_do_tieng_viet(proj):
    denied, why = bash(proj, "npx x")
    assert denied and any(ord(c) > 127 for c in why)


# ---------------------------------------------------------------- npm/pnpm: .npmrc
@pytest.mark.parametrize("cmd", PKG_OK)
def test_pkg_npmrc_goc_bi_chan(proj, cmd):
    (proj / ".npmrc").write_text("script-shell=/bin/sh\n", encoding="utf-8")
    assert bash(proj, cmd)[0], cmd
    assert bash(proj, cmd, "reviewer")[0], cmd


@pytest.mark.parametrize("sub", ["apps/web", "packages/db"])
def test_pkg_npmrc_workspace_bi_chan(proj, sub):
    (proj / sub).mkdir(parents=True)
    (proj / sub / ".npmrc").write_text("x=1\n", encoding="utf-8")
    assert bash(proj, "pnpm test")[0]
    assert bash(proj, "npm run build:web")[0]


def test_pkg_npmrc_thu_muc_dang_chay_bi_chan(proj):
    sub = proj / "apps" / "web"
    sub.mkdir(parents=True)
    (sub / ".npmrc").write_text("x=1\n", encoding="utf-8")
    data = {"tool_name": "Bash", "cwd": str(sub),
            "tool_input": {"command": "npm test", "description": DESC}}
    assert decision(run_raw(proj, json.dumps(data)))[0]


def test_pkg_npmrc_trong_node_modules_khong_chan(proj):
    d = proj / "node_modules" / "x"
    d.mkdir(parents=True)
    (d / ".npmrc").write_text("x=1\n", encoding="utf-8")
    assert bash(proj, "pnpm test") == (False, "")


def test_pkg_npmrc_khong_anh_huong_lenh_khac(proj):
    (proj / ".npmrc").write_text("x=1\n", encoding="utf-8")
    assert bash(proj, "git status") == (False, "")
    assert bash(proj, "python3 -m pytest -q tests") == (False, "")


# ---------------------------------------------------------------- .gitignore: executor không ghi ở gốc repo
def test_gitignore_goc_executor_che_do_branch_bi_chan(proj):
    setup_executor(proj)
    denied, why = write(proj, ".gitignore", "executor")
    assert denied and ".gitignore" in why


def test_gitignore_goc_executor_che_do_nhap_bi_chan(proj):
    setup_executor(proj, branch=False)
    assert write(proj, ".gitignore", "executor")[0]


def test_gitignore_trong_drafts_theo_che_do_nhap(proj):
    setup_executor(proj, branch=False)
    assert write(proj, f"drafts/{REQ}/multi-agent/.gitignore", "executor") == (False, "")
    assert write(proj, f"drafts/{REQ}/game-hub/.gitignore", "executor") == (False, "")
    assert write(proj, "sub/.gitignore", "executor")[0]  # ngoài drafts/: chế độ nháp cấm


def test_gitignore_trong_drafts_theo_che_do_branch(proj):
    setup_executor(proj)
    assert write(proj, f"drafts/{REQ}/x/.gitignore", "executor") == (False, "")
    assert write(proj, "src/.gitignore", "executor") == (False, "")  # không phải gốc repo


def test_gitignore_goc_cac_cach_viet_van_bi_chan(proj):
    setup_executor(proj)
    for path in ("./.gitignore", "src/../.gitignore"):
        assert write(proj, path, "executor")[0], path


def test_gitignore_goc_agent_khac_bi_chan(proj):
    for agent in (None, "designer", "reviewer", "analyst"):
        assert write(proj, ".gitignore", agent)[0], agent


def test_gitignore_executor_van_ghi_duoc_code_khac(proj):
    setup_executor(proj)
    assert write(proj, "src/x.py", "executor") == (False, "")
    assert write(proj, ".gitignore.bak", "executor") == (False, "")
