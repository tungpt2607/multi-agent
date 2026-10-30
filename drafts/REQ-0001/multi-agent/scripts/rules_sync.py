#!/usr/bin/env python3
"""Đồng bộ file luật từ repo gốc sang repo con (REQ-0001, P3).

  rules_sync.py check --source <repo gốc> --target <repo con>
  rules_sync.py apply --source <repo gốc> --target <repo con>

Chỉ dùng thư viện chuẩn. Không mạng, không xoá file, không shell.
Git chỉ gọi lệnh đọc: rev-parse, branch --show-current, status --porcelain.
Mã thoát: 0 khớp/thành công, 1 lệch, 2 thiếu, 3 lỗi đầu vào hoặc bị từ chối.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys

EXIT_OK, EXIT_DRIFT, EXIT_MISSING, EXIT_ERROR = 0, 1, 2, 3

# Thư mục không bao giờ được đọc/ghi
BLOCKED_ANY = {".git", ".claude", ".github"}
BLOCKED_AGENTS = {"knowledge", "requests", "tasks", "decisions",
                  "artifacts", "ledger", "state"}
MODE_RE = re.compile(r"^\s*mode:")
PROTECTED_BRANCHES = {"main", "master"}


class SyncError(Exception):
    """Lỗi đầu vào hoặc vi phạm điều kiện an toàn: dừng ngay."""


# ---------- đường dẫn an toàn ----------

def split_rel(rel):
    """Kiểm tra đường dẫn tương đối của mục manifest, trả về các thành phần."""
    if not isinstance(rel, str) or not rel.strip():
        raise SyncError("đường dẫn rỗng trong manifest")
    norm = rel.replace("\\", "/")
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or os.path.isabs(rel):
        raise SyncError("đường dẫn tuyệt đối bị chặn: " + rel)
    parts = norm.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise SyncError("đường dẫn chứa '..' hoặc rỗng bị chặn: " + rel)
    if any(p in BLOCKED_ANY for p in parts):
        raise SyncError("thư mục bị chặn (.git/.claude/.github): " + rel)
    if parts[0] == "knowledge" or (
            parts[0] == ".agents" and len(parts) > 1 and parts[1] in BLOCKED_AGENTS):
        raise SyncError("dữ liệu vận hành/knowledge bị chặn: " + rel)
    if any(c in p for p in parts[:-1] for c in "*?["):
        raise SyncError("ký tự glob chỉ được ở tên file cuối: " + rel)
    return parts


def full_path(root, parts):
    """Ghép đường dẫn, từ chối symlink ở bất kỳ cấp nào đã tồn tại."""
    cur = root
    for p in parts:
        cur = os.path.join(cur, p)
        if os.path.islink(cur):
            raise SyncError("symlink bị chặn: " + cur)
    real_root = os.path.realpath(root)
    if os.path.commonpath([real_root, os.path.realpath(cur)]) != real_root:
        raise SyncError("đường dẫn thoát khỏi thư mục gốc: " + cur)
    return cur


def read_text(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


# ---------- manifest ----------

def load_manifest(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise SyncError("không đọc được manifest: %s" % e)
    if not isinstance(data, dict):
        raise SyncError("manifest sai định dạng (cần một JSON object)")
    entries = data.get("entries")
    if data.get("version") != 1 or not isinstance(entries, list) or not entries:
        raise SyncError("manifest sai định dạng (cần version 1 và entries)")
    for e in entries:
        if not isinstance(e, dict):
            raise SyncError("mục manifest không phải JSON object")
        if e.get("kind") not in ("whole", "glob", "markers"):
            raise SyncError("kind không hợp lệ: %r" % e.get("kind"))
        split_rel(e.get("path"))
        if e["kind"] == "markers" and not e.get("marker"):
            raise SyncError("mục markers thiếu tên marker")
    return entries


def expand(source, entry):
    """Trả danh sách đường dẫn tương đối (dấu /) của một mục, lấy từ repo gốc."""
    parts = split_rel(entry["path"])
    if entry["kind"] != "glob":
        return ["/".join(parts)]
    d = full_path(source, parts[:-1])
    if not os.path.isdir(d):
        raise SyncError("thiếu thư mục nguồn: " + "/".join(parts[:-1]))
    out = []
    for name in sorted(os.listdir(d)):
        if not fnmatch.fnmatchcase(name, parts[-1]):
            continue
        p = os.path.join(d, name)
        if os.path.islink(p):
            raise SyncError("symlink bị chặn: " + p)
        if os.path.isfile(p):
            out.append("/".join(parts[:-1] + [name]))
    return out


# ---------- chuẩn hoá nội dung ----------

def find_block(lines, name):
    """Tìm cặp marker duy nhất; trả (chỉ số begin, chỉ số end) hoặc báo lỗi."""
    b_tag, e_tag = "rules-sync:begin " + name, "rules-sync:end " + name
    begins = [i for i, l in enumerate(lines) if b_tag in l]
    ends = [i for i, l in enumerate(lines) if e_tag in l]
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        raise SyncError("marker thiếu/trùng/lệch cặp: " + name)
    return begins[0], ends[0]


def normalize(entry, text):
    if entry["kind"] == "markers":
        lines = text.splitlines(True)
        b, e = find_block(lines, entry["marker"])
        return "".join(lines[b + 1:e])
    if entry.get("ignore_mode"):
        # chỉ bỏ đúng các dòng bắt đầu bằng `mode:`
        return "".join(l for l in text.splitlines(True) if not MODE_RE.match(l))
    return text


def digest(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------- git (chỉ đọc) ----------

def git_allowed(args):
    """Chỉ đúng: rev-parse <đối số đọc>, branch --show-current, status --porcelain."""
    if not args:
        return False
    if args[0] == "rev-parse":
        # đối số đọc: không cờ (-...), ví dụ `HEAD`
        return len(args) > 1 and all(
            isinstance(a, str) and a and not a.startswith("-") for a in args[1:])
    if args[0] == "branch":
        return tuple(args) == ("branch", "--show-current")
    if args[0] == "status":
        return tuple(args) == ("status", "--porcelain")
    return False


def git_read(target, *args):
    if not git_allowed(args):
        raise SyncError("lệnh git không được phép: " + " ".join(args))
    try:
        r = subprocess.run(["git", "-C", target] + list(args),
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise SyncError("không chạy được git: %s" % e)
    if r.returncode != 0:
        raise SyncError("git %s lỗi: %s" % (args[0], r.stderr.strip()))
    return r.stdout.strip()


# ---------- check ----------

def compare(source, target, entry, rel):
    """Trả (trạng thái, ghi chú) cho một file."""
    parts = rel.split("/")
    s_path = full_path(source, parts)
    if not os.path.isfile(s_path):
        raise SyncError("thiếu file nguồn: " + rel)
    s_norm = normalize(entry, read_text(s_path))
    t_path = full_path(target, parts)
    if not os.path.isfile(t_path):
        return "MISSING", ""
    try:
        t_norm = normalize(entry, read_text(t_path))
    except SyncError as e:
        if entry["kind"] == "markers":
            return "MISSING", str(e)
        raise
    return ("OK" if digest(s_norm) == digest(t_norm) else "DRIFT"), ""


def cmd_check(source, target, entries):
    results = []
    for entry in entries:
        for rel in expand(source, entry):
            results.append((rel,) + compare(source, target, entry, rel))
    ok = 0
    for rel, status, note in results:
        print("%s: %s%s" % (status, rel, " (%s)" % note if note else ""))
        ok += status == "OK"
    print("%s: %d/%d mục khớp" % ("OK" if ok == len(results) else "KẾT QUẢ",
                                  ok, len(results)))
    statuses = {s for _, s, _ in results}
    if "MISSING" in statuses:
        return EXIT_MISSING
    if "DRIFT" in statuses:
        return EXIT_DRIFT
    return EXIT_OK


# ---------- apply ----------

def build_new_text(source, target, entry, rel):
    """Tính nội dung mới cho file đích (chưa ghi). Trả (đường dẫn, cũ, mới)."""
    parts = rel.split("/")
    s_text = read_text(full_path(source, parts))
    t_path = full_path(target, parts)
    t_exists = os.path.isfile(t_path)
    t_text = read_text(t_path) if t_exists else None

    if entry["kind"] == "markers":
        if not t_exists:
            raise SyncError("đích thiếu file có marker: " + rel)
        s_lines, t_lines = s_text.splitlines(True), t_text.splitlines(True)
        sb, se = find_block(s_lines, entry["marker"])
        tb, te = find_block(t_lines, entry["marker"])
        new = "".join(t_lines[:tb + 1] + s_lines[sb + 1:se] + t_lines[te:])
    elif entry.get("ignore_mode"):
        if not t_exists:
            raise SyncError("đích thiếu file, không biết dòng mode: để giữ: " + rel)
        s_lines = s_text.splitlines(True)
        t_modes = [l for l in t_text.splitlines(True) if MODE_RE.match(l)]
        s_idx = [i for i, l in enumerate(s_lines) if MODE_RE.match(l)]
        if len(s_idx) != len(t_modes):
            raise SyncError("số dòng mode: nguồn/đích khác nhau: " + rel)
        for i, line in zip(s_idx, t_modes):
            s_lines[i] = line  # giữ dòng mode: của repo con
        new = "".join(s_lines)
    else:
        new = s_text
    return t_path, t_text, new


def cmd_apply(source, target, entries):
    # 1. Kiểm trước: nhánh, cây sạch
    sha7 = git_read(source, "rev-parse", "HEAD")[:7]
    branch = git_read(target, "branch", "--show-current")
    if branch in PROTECTED_BRANCHES or not branch:
        raise SyncError("từ chối: repo con đang ở nhánh %r" % branch)
    if branch != "rules-sync/" + sha7:
        raise SyncError("từ chối: cần nhánh rules-sync/%s, đang ở %r" % (sha7, branch))
    if git_read(target, "status", "--porcelain"):
        raise SyncError("từ chối: repo con còn thay đổi chưa commit")

    # 2. Tính hết nội dung trước, lỗi ở đâu cũng dừng trước khi ghi
    plan = []
    for entry in entries:
        for rel in expand(source, entry):
            path, old, new = build_new_text(source, target, entry, rel)
            if old != new:
                plan.append((rel, path, new))

    # 3. Ghi đúng các mục đã tính
    for rel, path, new in plan:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)
    if plan:
        print("Đã ghi %d file:" % len(plan))
        for rel, _, _ in plan:
            print("  " + rel)
    else:
        print("Không có file nào thay đổi.")
    print("Chưa commit/push. Chạy `check` rồi tự commit.")
    return EXIT_OK


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Đồng bộ file luật (check/apply)")
    ap.add_argument("command", choices=["check", "apply"])
    ap.add_argument("--source", required=True, help="repo gốc")
    ap.add_argument("--target", required=True, help="repo con")
    ap.add_argument("--manifest", default=os.path.join(here, "rules_manifest.json"))
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return EXIT_ERROR if e.code else EXIT_OK
    try:
        for d in (args.source, args.target):
            if not os.path.isdir(d):
                raise SyncError("không phải thư mục: " + d)
        entries = load_manifest(args.manifest)
        fn = cmd_check if args.command == "check" else cmd_apply
        return fn(args.source, args.target, entries)
    except SyncError as e:
        print("LỖI: %s" % e, file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
