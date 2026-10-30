# Hệ thống Multi-Agent xử lý quy trình phần mềm

Bộ 9 agent chạy trên Claude Code: phiên chính là **Điều phối**, 8 subagent chuyên trách. Bạn là người quyết định duy nhất; mọi lựa chọn hiện thành ô bấm.

## Bắt đầu
1. Cài Claude Code, mở thư mục này (`claude` trong terminal, hoặc mở repo trên app Claude).
2. Gõ yêu cầu, ví dụ: `Yêu cầu mới: thêm đăng nhập Google`.
3. Bấm chọn ở mỗi phiếu. Điều phối không bao giờ tự chọn.

## Cấu trúc
| Đường dẫn | Vai trò |
| --- | --- |
| `CLAUDE.md` | Luật của Điều phối |
| `.claude/agents/*.md` | 8 subagent: intake, analyst, designer, planner, executor, reviewer, guard, reporter |
| `.claude/hooks/gate.py` | Cổng duyệt + Guard lớp 1, chặn bằng code |
| `.claude/hooks/record_answer.py` | Ghi nguyên văn câu trả lời ô bấm của bạn vào `.agents/ledger/` |
| `.agents/workflow.yaml` | Quy trình 7 bước, hạng mô hình, ngân sách token |
| `.agents/schemas/` | 4 JSON Schema: Request, Task, DecisionRequest, Artifact |
| `.agents/knowledge/` | Quy tắc nghiệp vụ, chuẩn code — chỉ bạn ghi |
| `drafts/<REQ>/` | Nơi duy nhất agent Thực thi được ghi (chế độ nháp) |

## Hook chặn những gì
- Agent sửa luật, hook, schema, quy trình, kho tri thức, sổ câu trả lời.
- Subagent ghi trạng thái hoặc sổ quyết định; sửa phiếu đã trả lời.
- Thực thi ghi ngoài `drafts/<REQ>/`, hoặc khi phiếu chưa có câu trả lời gốc của bạn.
- Lệnh shell: git push/merge/reset, xoá đệ quy, gọi mạng, deploy, ghi file bằng shell, đọc khoá bí mật; lệnh không kèm giải thích tiếng Việt; subagent khác Kiểm tra chạy shell.

Mọi lần gọi công cụ ghi/shell được log ở `.agents/state/tool_log.jsonl`.

## Giới hạn hiện tại
- `max_tokens` của subagent được giữ bằng hướng dẫn và `maxTurns`; Claude Code chưa có trường giới hạn đầu ra cho subagent.
- `token_used` do Điều phối ước lượng.
- Chưa có công cụ tác động thật (merge, deploy, email, CRM) — giai đoạn 3.

## Kiểm tra hook
`python3 tests/test_gate.py` — 28 tình huống chặn/cho phép.
