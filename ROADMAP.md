# AnkiVN Image Picker — Roadmap

Danh sách bug, design issue và missing features được phân loại theo mức ưu tiên. Cập nhật khi có thêm phát hiện hoặc khi item được hoàn thành.

Ký hiệu trạng thái: `[ ]` chưa làm · `[x]` đã xong · `[~]` đang làm · `[?]` cần thảo luận thêm

---

## 🔴 Cao — Bug thật ảnh hưởng user

- [ ] **#7 — `editor.saveNow()` trước khi mutate field**  
  Nếu user đang gõ trong field đang được insert, value cũ trên `note.fields[i]` chưa flush từ web view → ghi đè input của user. Cần gọi `editor.saveNow(callback)` trước khi `note.fields[i] = existing + tag`.  
  *File: `editor_bridge.py::insert_image`*

- [ ] **#3 — `_batch_download_pool` leak**  
  Pool không bao giờ shutdown khi batch thành công, chỉ shutdown trong `closeEvent`. Nếu user mở/đóng nhiều batch trong 1 phiên Anki → pool tích lũy.  
  Fix: shutdown trong `_advance_batch()` khi queue exhausted, hoặc track lifecycle riêng.  
  *File: `picker_dialog.py`*

- [ ] **#4 — `setMinimumSize(1100, 750)` không reset**  
  Sau khi đóng batch dialog, lần sau mở single-note dialog vẫn bị forced 1100×750 (do constraint còn trên class instance? — cần verify thực tế). User màn hình nhỏ khó dùng.  
  Fix: clear minSize trong `closeEvent` hoặc set per-instance.  
  *File: `picker_dialog.py::start_batch`*

- [ ] **#11 — Warn khi overwrite ảnh đã có**  
  User click ảnh trên note có badge ✅ → addon append thêm ảnh vào field đã có. Có thể không phải intent.  
  Fix: dialog confirm "Note này đã có ảnh, append thêm hay thay thế?"  
  *File: `picker_dialog.py::on_image_clicked` (batch path)*

- [ ] **#1 — Browser preview stale sau insert trong batch mode**  
  Trong batch, `_BatchEditorShim.loadNoteKeepingFocus` là no-op. Nếu user mở Browser preview pane song song với batch → preview không update. Hiện tại browser refresh chỉ chạy lúc kết thúc batch.  
  Fix: gọi `browser.model.reset()` hoặc emit `editor_did_load_note` sau mỗi note insert.  
  *File: `editor_bridge.py` + `browser_menu.py::_BatchEditorShim`*

- [ ] **#2 — Race condition khi double-click nhanh**  
  Trong batch mode `on_image_clicked` không lock (intentional cho fast-forward). Nhưng `_batch_jobs[result.full_url]` dùng URL làm key — nếu 2 ảnh có URL trùng (Wikimedia same file edge case) → ghi đè.  
  Fix: key = UUID per click hoặc `(provider_id, url, click_idx)`.  
  *File: `picker_dialog.py::on_image_clicked`*

---

## 🟡 Trung — Design issue / UX rõ rệt

- [ ] **#9 — `_batch_jobs` keying bằng URL**  
  Sai về concept — URL không unique giữa các provider/click. Nên là dict với key UUID.  
  *File: `picker_dialog.py`*

- [ ] **#10 — Prefetch cache `pop()` mất data khi re-jump**  
  User jump tới note 5 → đi tiếp → click lại note 5 → phải search từ đầu. Nên giữ LRU cache 32 queries thay vì pop ngay.  
  *File: `browser_menu.py::_run_picker_for_note`*

- [ ] **#12 — Không có undo cho batch**  
  Click sai ảnh → không revert được. Nên có undo stack ít nhất 5 actions per batch.

- [ ] **#13 — `auto-search` checkbox default OFF**  
  User click note đầu tiên không thấy gì xảy ra → confused. Nên default ON.  
  *File: `picker_dialog.py::_REMEMBERED_AUTO_SEARCH`*

- [ ] **#14 — Splitter `setChildrenCollapsible(True)` gây kẹt**  
  User kéo collapse panel notes → khôi phục cực khó. Nên `setCollapsible(0, False)` hoặc thêm nút `Hide/Show Notes`.  
  *File: `picker_dialog.py::_setup_ui`*

- [ ] **#15 — Status bar quá tải thông tin**  
  Hiện tại: `Total: 176 | unsplash: 30 · pexels: 80 · openverse: 20 · wikimedia: 46 | 🖼 153 shown · 157/176 (89%) · 4 hidden | 📦 Prefetched 0/98 notes · 11 in flight`  
  Quá nhiều text. Gom vào tooltip hoặc bỏ thông tin trùng.

- [ ] **#16 — Search history**  
  Dropdown lịch sử query trong session để user quay lại nhanh.

- [ ] **#5 — Geometry remember không tách batch / single**  
  Maximize batch dialog → lần sau mở single-note cũng maximize. Nên có 2 geometry slot riêng.  
  *File: `picker_dialog.py::_REMEMBERED_GEOMETRY`*

---

## 🟢 Thấp — Missing features đáng làm

- [ ] **#17 — Right-click context menu trên ảnh**  
  Preview lớn, copy URL, mở source page, blacklist provider/result.

- [ ] **#18 — Keyboard navigation**  
  - `Space` = pick selected image
  - `Enter` = search
  - `Esc` = skip
  - `Ctrl+→` = next note
  - `Ctrl+←` = previous note
  - Arrow keys = navigate grid

- [ ] **#19 — Filter ảnh theo aspect ratio / size**  
  Lọc client-side: chỉ landscape, chỉ portrait, min 800x600...

- [ ] **#20 — Toggle provider realtime trong dialog**  
  Tạm tắt 1 provider mà không cần vào Settings.

- [ ] **#21 — Resize ảnh trước khi insert**  
  Option auto-resize về max 1024px hoặc max 500KB. Giảm size collection, sync nhanh hơn.

- [ ] **#22 — Validate API key trước batch**  
  Test 1 query nhỏ với mỗi provider trước khi mở batch. Báo "unsplash rate-limited" sớm.

- [ ] **#23 — Resume batch sau crash**  
  Save batch state vào `user_files/batch_state.json` mỗi pick. Khởi động lại có dialog hỏi "Resume previous batch?"

- [ ] **#24 — Multi-image insert per note**  
  Hiện chỉ 1 ảnh/note. Nhiều user muốn 2-3 ảnh.

- [ ] **#25 — Attribution lưu trong field metadata thay vì HTML inline**  
  Inline HTML có thể vỡ khi export sang Mochi, RemNote, etc.

---

## 🧹 Tech debt

- [ ] **#8 — 18 test failures baseline**  
  Phần lớn là test stub thiếu method (`setToolTip`, `setTextFormat`...). Test khác fail vì:
  - Provider parsing test expect URL không có UTM params (nhưng addon thêm UTM cho Unsplash compliance)
  - Smoke config keys test còn refer `pixabay_api_key`, `google_cse_id`, `google_api_key` (provider đã removed)
  - Smoke default test expect `providers == ["unsplash"]` và `max_results_per_provider == 12` (đã đổi)
  
  Fix riêng 1 buổi để CI có ý nghĩa.

- [ ] **`tests/unit/test_pixabay_provider.py` orphan**  
  Module `pixabay` đã xóa nhưng test còn → import error. Xóa test hoặc viết lại.

---

## 🎯 Đề xuất thứ tự thực hiện

Sprint 1 (bug fixes nhanh, < 2 commits):
1. #14 — `setCollapsible(0, False)` cho notes panel
2. #4 — clear minSize khi không batch
3. #3 — pool leak shutdown
4. #13 — auto-search default ON

Sprint 2 (correctness):
5. #7 — saveNow trước insert
6. #11 — warn overwrite
7. #1 — browser preview refresh
8. #2 + #9 — UUID keying cho batch_jobs

Sprint 3 (UX):
9. #18 — keyboard shortcuts
10. #21 — resize trước insert
11. #15 — status bar gọn lại

Sprint 4 (features lớn):
12. #19 — filter aspect/size
13. #24 — multi-image per note
14. #23 — resume batch

Test debt (#8): nên xen kẽ làm cùng các sprint trên, fix dần khi đụng module liên quan.
