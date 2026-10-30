# APPLY.md: áp dụng REQ-0001 (người dùng tự chạy)

Ký hiệu: `MA` = `/home/user/multi-agent`, `GH` = `/home/user/tungpt2607/game-hub` (đổi nếu repo thật ở chỗ khác). Mỗi lệnh ghi rõ chạy ở repo nào. Không dùng: rm -rf, reset --hard, git clean, push --force, branch -D, git merge, gh pr merge, --no-verify. Không đưa secret vào file hay lệnh.

## Trước khi bắt đầu
Đọc script trước khi chạy: `MA/drafts/REQ-0001/multi-agent/scripts/rules_sync.py` (`check` chỉ đọc; `apply` ghi file trong repo con, chỉ ở nhánh `rules-sync/<sha7>` và cây sạch) và `rules_manifest.json`. Lưu ý: manifest có `.agents/schemas/*.json`; `apply` sẽ chép schema từ MA sang GH nếu khác, xem kết quả `check` trước khi apply.

## P6: multi-agent (chạy ở MA)
```bash
cd /home/user/multi-agent
git status --short                      # cây phải sạch (drafts/ có thể untracked)
git switch -c agent/REQ-0001            # nếu nhánh đã có: git switch agent/REQ-0001
git apply --check drafts/REQ-0001/multi-agent/.agents/workflow.yaml.patch
git apply --check drafts/REQ-0001/multi-agent/CLAUDE.md.patch
git apply drafts/REQ-0001/multi-agent/.agents/workflow.yaml.patch
git apply drafts/REQ-0001/multi-agent/CLAUDE.md.patch
mkdir -p scripts
cp drafts/REQ-0001/multi-agent/scripts/rules_manifest.json \
   drafts/REQ-0001/multi-agent/scripts/rules_sync.py \
   drafts/REQ-0001/multi-agent/scripts/test_rules_sync.py scripts/
(cd scripts && python3 -m unittest test_rules_sync -v)   # phải OK
git add .agents/workflow.yaml CLAUDE.md scripts/rules_manifest.json scripts/rules_sync.py scripts/test_rules_sync.py
git status --short                      # chỉ 5 file trên được staged
git commit -m "REQ-0001: pre_gate, Minh bạch agent, scripts đồng bộ luật"
git push -u origin agent/REQ-0001       # TUỲ CHỌN: chỉ khi bạn muốn mở PR; bỏ qua nếu không
```
Mở PR (tuỳ chọn) (GitHub web hoặc `gh pr create --base <nhánh chính> --head agent/REQ-0001 --title "REQ-0001: minh bạch agent + rules_sync" --body "File: .agents/workflow.yaml, CLAUDE.md, scripts/ (3 file)"`), đọc diff, tự duyệt theo quyết định của bạn. Nếu `git apply --check` lỗi: dừng, không ép apply.

Lấy sha7 cho P7: `git rev-parse --short=7 HEAD` (ở MA, sau commit P6; nếu bạn merge PR thì lấy HEAD của nhánh chính sau đó, tự làm trên web).

## P7: game-hub (chạy ở GH)
Thay `<sha7>` bằng giá trị ở trên.
```bash
cd /home/user/tungpt2607/game-hub
git status --short                      # phải sạch
git switch -c rules-sync/<sha7>
python3 /home/user/multi-agent/scripts/rules_sync.py check --source /home/user/multi-agent --target /home/user/tungpt2607/game-hub   # dự kiến: thiếu marker CLAUDE.md, exit 2
# Bước khởi tạo CLAUDE.md (chỉ khi CLAUDE.md game-hub còn giống bản gốc của MA; nếu --check lỗi thì DỪNG, không ép):
git apply --check /home/user/multi-agent/drafts/REQ-0001/multi-agent/CLAUDE.md.patch
git apply /home/user/multi-agent/drafts/REQ-0001/multi-agent/CLAUDE.md.patch
git add CLAUDE.md                       # apply yêu cầu cây làm việc sạch, nên phải commit CLAUDE.md trước
git commit -m "REQ-0001: thêm marker minh-bach-agent vào CLAUDE.md"
python3 /home/user/multi-agent/scripts/rules_sync.py apply --source /home/user/multi-agent --target /home/user/tungpt2607/game-hub
python3 /home/user/multi-agent/scripts/rules_sync.py check --source /home/user/multi-agent --target /home/user/tungpt2607/game-hub   # phải OK (exit 0)
git diff --stat                         # chỉ còn .agents/workflow.yaml (CLAUDE.md đã được commit ở bước trước; thêm schemas nếu có lệch); mode: branch giữ nguyên
git add .agents/workflow.yaml CLAUDE.md   # thêm .agents/schemas/<file> nếu diff có
git commit -m "rules-sync <sha7>: pre_gate + Minh bạch agent"
git push -u origin rules-sync/<sha7>    # TUỲ CHỌN, như P6
```
Lưu ý: `git apply` bắt buộc trước `apply` vì `apply` dừng (exit 3, không ghi gì) khi CLAUDE.md đích chưa có marker. Nếu CLAUDE.md game-hub đã khác bản gốc của MA thì `--check` sẽ lỗi: dừng và tự xử lý tay. Từ nay, khối marker `minh-bach-agent` trong CLAUDE.md của repo con (gồm cả mục "Xin phép trước mỗi bước", G1/G2) bị `rules_sync.py apply` ghi đè theo nguồn MA; sửa tay trong khối đó ở repo con sẽ mất. Phần ngoài marker không bị đụng (bước 3 ở "Vòng điều phối" chỉ là con trỏ tới mục trong khối).

Mở PR vào nhánh chính của game-hub (web hoặc `gh pr create --base <nhánh chính> --head rules-sync/<sha7> --title "rules-sync <sha7>" --body "File: .agents/workflow.yaml, CLAUDE.md"`), kiểm tra diff rồi tự duyệt. Đối chiếu với bản nháp: `drafts/REQ-0001/game-hub/` (kết quả sau apply phải trùng nội dung).

## Hoàn tác (không phá huỷ)
- Chưa commit: ở repo đó `git restore <file>` cho từng file đã sửa; file mới của scripts/ thì xoá từng file bằng `rm <file>` (không dùng rm -rf).
- Đã commit, chưa push: `git switch <nhánh chính>`; nhánh làm việc để đó, bỏ không dùng.
- Đã push/mở PR: đóng PR trên web (không merge); để nhánh trên remote hoặc xoá trên web.
- Đã merge (bạn tự làm): tạo nhánh mới và `git revert <sha>` qua PR.
