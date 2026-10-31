# APPLY.md: áp bản vá gate (REQ-0002, TASK-0025)

Agent không ghi được `.claude/` và `CLAUDE.md` (chủ ý). Bạn tự chép. Đọc `ART-0024` (điều kiện) và so diff trước khi chép.

## 1. Tệp và đích

| Nháp (trong `drafts/REQ-0002/`) | Chép vào | Ghi chú |
| --- | --- | --- |
| `.claude/hooks/gate.py` | `.claude/hooks/gate.py` | Viết lại (fail closed, allowlist, phiếu một lần) |
| `.claude/settings.json` | `.claude/settings.json` | Thêm matcher `mcp__github__.*` và matcher PreToolUse `AskUserQuestion` (L5; PostToolUse `record_answer` giữ nguyên) |
| `CLAUDE.md` | `CLAUDE.md` | Chỉ 2 thay đổi, xem mục 3 |
| `tests/test_gate.py` | `tests/test_gate.py` | Test, chỉ dùng thư mục tạm |

`record_answer.py` giữ nguyên. Nên chép lúc không có agent đang chạy.

## 2. sha256 (reviewer điền lại)

Sau lần sửa 2 (TASK-0028) mọi hash cũ đã lỗi thời. Executor không có shell nên không tính được. Reviewer chạy `sha256sum` trên bản nháp rồi điền lại; bạn tự tính lại trên tệp đã chép để đối chiếu.

| Tệp | sha256 |
| --- | --- |
| `.claude/hooks/gate.py` | reviewer điền lại |
| `.claude/settings.json` | reviewer điền lại |
| `CLAUDE.md` | reviewer điền lại |
| `tests/test_gate.py` | reviewer điền lại |
| `APPLY.md` | reviewer điền lại |

## 2b. Thứ tự áp dụng (L2)

1. Merge bản vá vào `main` trước (nhánh nào đang chứa gate cũ đều là fail open).
2. Sau khi áp, không `git switch` sang nhánh còn gate cũ (ví dụ `agent/REQ-0001`...). Kiểm: `git diff main <nhánh> -- .claude CLAUDE.md` phải rỗng. Đây là biện pháp thủ công, không phải chốt kỹ thuật: gate cho `git switch main|agent/REQ-xxxx` và gate nằm trong git, nên switch sang nhánh cũ âm thầm quay về gate cũ.
3. Thử ngay một phiếu thật (ô bấm `AskUserQuestion`) để chắc L5 không chặn nhầm (mẫu `tool_input` thật chưa được xác nhận có/không khoá `answers` ở đầu vào). Nếu bị chặn nhầm: chép ngược các `.bak` (mục 4, 5) rồi báo lại.

## 3. Diff CLAUDE.md phải đúng 2 chỗ

So với `drafts/REQ-0001/multi-agent/CLAUDE.md`:
1. Mục 5 của "Bước Thực thi": câu "Không push, không merge — người dùng tự làm." được thay bằng luật phiếu.
2. Thêm mục 6: "Sau mỗi phiếu `approve_stage`, Điều phối commit `.agents/` trên nhánh `agent/<REQ-id>` (không push)."

Nếu diff có chỗ nào khác, không chép.

## 4. Giữ gate cũ để quay lại (chỉ là hướng dẫn)

Trước khi chép, tự làm các bước sau (agent không chạy giúp):
```
cp .claude/hooks/gate.py .claude/hooks/gate.py.bak
cp .claude/settings.json .claude/settings.json.bak
cp CLAUDE.md CLAUDE.md.bak
```
Chép nháp vào, rồi chạy kiểm tra:
```
python3 -m pytest tests/test_gate.py -q
```
Test chỉ dùng thư mục tạm. Khi test bản nháp chưa chép, đặt `GATE_PATH=drafts/REQ-0002/.claude/hooks/gate.py` ở shell của bạn (gate không cho Điều phối/reviewer dùng `X=y lệnh`). Mặc định test dùng gate cạnh thư mục `tests/`.

## 5. Quay lại

Chép ngược `.bak` (hoặc `git checkout -- .claude CLAUDE.md` nếu chưa commit; `git revert` nếu đã commit). Xoá `.agents/ledger/consumed.jsonl` chỉ khi bạn muốn phiếu cũ dùng lại được (không nên).

## 6. Hành vi mới cần biết

- Fail closed: JSON hỏng, lỗi nội bộ, lệnh không phân tích được đều bị từ chối.
- Bash chỉ cho allowlist ART-0022 Điểm 1. Cấm: `$()`, backtick, `<()`, `eval`, `bash -c`, `python3 -c`, `X=y lệnh`, `git -C/-c`, `find -exec`, chuyển hướng ra file (chỉ `>/dev/null`, `2>/dev/null`, `2>&1`), đường dẫn tuyệt đối/`..` ra ngoài dự án, file bí mật. Glob như `*` có thể khớp tên bí mật (id_rsa...) nên bị chặn: hãy nêu tên file.
- Vân tay phiếu (L4): `{{PUSH <remote> <nhánh>}}`; `{{MERGE <nguồn> -> <nhánh hiện tại>}}` (đọc `.git/HEAD`; detached = deny); MCP theo tool, `<o/r>` = owner/repo, tên tool là phần sau `mcp__github__`:
  - `merge_pull_request`, `update_pull_request`, `update_pull_request_branch`, `pull_request_review_write`, `request_copilot_review`, `add_comment_to_pending_review`, `add_reply_to_pull_request_comment`: `{{MCP <tool> <o/r> #<pullNumber>}}`
  - `push_files`: `{{MCP push_files <o/r> <branch>}}`, thêm ` <path>` nếu `files` có đúng một phần tử; `create_or_update_file`, `delete_file`: `{{MCP <tool> <o/r> <branch> <path>}}`
  - `create_branch`: `{{MCP create_branch <o/r> <from_branch>-><branch>}}` (không nêu `from_branch` thì là `-`); `create_pull_request`: `{{MCP create_pull_request <o/r> <head>-><base>}}`
  - tool ghi khác/lạ: `{{MCP <tool> <o/r>}}` (thêm ` #<số>` nếu có `pullNumber`/`issue_number`).
  - Tool ghi ở nhóm trên mà thiếu trường định danh (owner/repo, pullNumber, branch, path, head/base) bị deny, không rơi về vân tay thô.
  - **Phiếu theo vân tay cũ (`{{MERGE <nhánh>}}`, `{{MCP <tool> <o/r>}}` cho các tool trên) không còn khớp**: phải mở lại phiếu với vân tay mới. Đây là hành vi đúng.
  Câu hỏi phải bắt đầu bằng `[DEC-xxxx]`; nhãn bạn bấm phải bắt đầu bằng "Đồng ý". Mỗi DEC dùng một lần (ghi vào `.agents/ledger/consumed.jsonl`, chỉ gate ghi).
- Lệnh đọc (L1, L3): mỗi lệnh trong allowlist có whitelist tuỳ chọn tường minh (`OPTS` trong gate.py), so khớp chính xác, không viết tắt (`sort --o=`, `date --se=`, `file --comp` bị deny). Cấm đọc đệ quy: `grep -r/-R/--recursive` (kể cả `-rn`), `rg -u/--hidden/--no-ignore/--follow`, `diff -r`, `diff`/`rg` trên thư mục; `rg` phải nêu file cụ thể; `find` chỉ cho vị từ trong whitelist (không `-exec/-delete/-fprint`). `date` chỉ nhận `+FORMAT`.
- L5: `AskUserQuestion` bị deny khi `tool_input` có `answers`/`annotations` không rỗng; vắng hoặc rỗng thì cho qua.

## 7. Phần chưa phủ / cần xác nhận trong phiên thật

- Tên tool MCP: danh sách tool đọc trong `MCP_READ` (gate.py) lấy từ đặc tả nhiệm vụ, chưa đối chiếu với tool github thật. Tool đọc có tên khác sẽ bị chặn nhầm (thêm vào `MCP_READ`); tool ghi lạ bị chặn (fail closed).
- Matcher chỉ phủ `mcp__github__.*`. MCP server khác (ví dụ `mcp__Claude_Docs__*`) vẫn không qua gate. Muốn phủ toàn bộ, đổi matcher thành `mcp__.*` và bổ sung logic cho server khác.
- Định dạng `tool_response` của AskUserQuestion: gate đọc `response.answers` dạng `{câu hỏi: nhãn}` (theo ghi chú ART-0022). Nếu bản Claude Code của bạn ghi khác, mọi phiếu bị từ chối (an toàn nhưng vô dụng): kiểm một dòng `answers.jsonl` thật.
- Trước khi bản này được áp, MCP và các lệnh Bash vẫn chưa qua gate mới; phiếu thủ công là bắt buộc.
- `pytest` chạy mã Python trong `tests/` (rủi ro đã chấp nhận ở DEC-0050).
