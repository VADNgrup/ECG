# Nguyên tắc dựng STRUCTURAL layer (heading tree) — ĐỌC TRƯỚC KHI SỬA

> Tài liệu này ghi lại **quy tắc bất di bất dịch** cho việc suy ra cấu trúc tài liệu (title → heading các
> cấp) trong `src/rag/structural.py`. Mục đích: **không bao giờ đi sai hướng** (đặc biệt là hardcode). Sửa
> bất cứ thứ gì liên quan heading → đọc file này trước.

## 0. QUY TẮC VÀNG — KHÔNG BAO GIỜ HARDCODE
Cấu trúc phải được suy ra từ **tín hiệu của CHÍNH tài liệu**, KHÔNG BAO GIỜ từ danh sách từ khoá / định
dạng riêng của một cuốn sách, một thể loại, hay một ngôn ngữ.

- ❌ SAI: `if title in {"Figure", "Table", "Chapter 6 Spark Streaming"}` — blocklist keyword.
- ❌ SAI: `re.match(r"^(Figure|Table|Note)\b", t)` — bắt theo CHỮ cụ thể (chỉ đúng tiếng Anh, sách kỹ thuật).
- ✅ ĐÚNG: nhận diện theo **HÌNH DẠNG/typography tổng quát** (bullet, "nhãn + số thứ tự N‑M", kí tự code,
  độ dài câu) hoặc theo **cấu trúc tài liệu tự khai báo** (printed table of contents).

Nếu một cách sửa chỉ đúng cho `apache_spark` mà sai cho "một cuốn văn học / luật / báo cáo tiếng Việt" thì
nó SAI. Đổi logic, đừng vá bằng keyword.

## 0.b. Ngoại lệ QUAN TRỌNG — Embedded outline = TOC-backbone (2026-08)
Nguyên tắc "KHÔNG synthesize node theo page-number của TOC" (mục 8 dưới) chỉ đúng cho **printed TOC OCR**
(page in ≠ `page_idx` vật lý, offset đổi theo chương). Nhưng **embedded PDF outline** (`fitz.get_toc()`)
trả **page vật lý 1-based** → tin cậy được. Nên với doc CÓ embedded outline (≥3 entry),
`build_kg._embedded_outline_entries` + `structural.build_structural_index_from_toc` lấy outline làm
**xương sống NGUYÊN SI**: MỖI entry (trừ nav thuần) = 1 heading node với **level + thứ tự + page lấy
Y HỆT get_toc** — KHÔNG re-level/prune/clamp/"sửa hậu kỳ". `_locate_toc_headings` tìm ranh giới content
bằng cách match tiêu đề **CHỈ trên đúng page TOC của entry đó** (`page-1-page_offset`, không dùng cửa sổ
liên trang) → tiêu đề trùng ở trang khác KHÔNG thể làm trôi ranh giới (vd "The Machine Learning Landscape"
ra 2 node: chương ở p24 và lời-giải-bài-tập ở p498, tách đúng). Gán MỌI block giữa 2 heading liên tiếp cho
heading trước → đủ content, heading cùng trang (35% trường hợp) vẫn tách content riêng, không lọt heading
giả (code/image/prose). Đây là đường MẶC ĐỊNH cho doc có embedded outline; bỏ qua hẳn
`extract_headings`/parser-`text_level`. Doc KHÔNG có embedded outline vẫn đi đường heuristic cũ (printed-TOC
page number vẫn KHÔNG tin). Sidecar `asset_headings.json` gán image/table/equation về heading theo page
(dùng chung per_item map). Verify HOML: 357 node, **0 drift** (start page mỗi node == page get_toc).

## 1. Thứ tự tin cậy (authority order)
0. **Embedded PDF outline** (`fitz.get_toc`, nếu có ≥3 entry) — page vật lý tin cậy → dùng làm backbone
   TOÀN PHẦN (xem mục 0.b). Các mục 1-3 dưới đây áp dụng cho doc KHÔNG có embedded outline.
1. **Printed Table of Contents** — cấu trúc do CHÍNH tác giả khai báo. Đáng tin nhất. `_toc_levels`
   (trong `build_kg.py`) parse TOC in → `{normalized_title: level}`. Heading khớp TOC = chắc chắn thật
   (dùng làm **lưới an toàn**: đã khớp TOC thì GIỮ, kể cả khi trông "giống prose").
2. **Cây heading từ parser** (`text_level` của MinerU) — CÓ NHIỄU, không được tin tuyệt đối. MinerU tag cả
   figure caption / "Note …" / code / bullet là `text_level`. Chỉ nhận làm heading nếu **qua bộ lọc noise
   typographic tổng quát** (mục 3).
3. **Suy từ KG** (global-mode query) — chỉ khi tài liệu KHÔNG có TOC lẫn heading (poster, scan phẳng).

## 2. Node model (generic, đã chốt)
Một type `heading` duy nhất + thuộc tính `level`: **level 0 = title cả tài liệu**, 1,2,3… nhỏ dần, sâu tuỳ
tài liệu. KHÔNG hardcode "chapter"/"section". (Chi tiết: `structural.py` docstring, `EXTRACT_PIPELINE.md`.)

## 3. Được phép dùng (tín hiệu TỔNG QUÁT) vs KHÔNG được
**ĐƯỢC** (khái quát mọi tài liệu):
- **TOC-match**: norm(title) khớp một mục TOC (sau khi strip prefix đánh số) → heading thật.
- **Bullet**: bắt đầu bằng kí tự bullet/list (`• · ▪ ◦ ‣ - *`) → không phải heading.
- **Numbered-float caption**: dạng `<nhãn> <số thứ tự phân cấp N-M / N.M>` (+ mô tả) → caption của hình/
  bảng/listing. Bắt theo **hình dạng số thứ tự**, KHÔNG theo chữ "Figure/Table".
- **Code**: chứa kí tự code `= ( ) { } ; < >` hoặc prompt (`scala>`, `>>>`). → không phải heading.
- **Prose/câu**: quá dài (> ~10 từ) hoặc kết thúc bằng dấu câu và > ~6 từ ("Note If a different version…").
- **Running-header**: cùng text lặp trên nhiều trang DÀY (mật độ trang) — đã có trong `extract_headings`.
- **Body-fragment** (`_looks_fragment`): text_level không-TOC mà **chữ cái CÓ-CASE đầu tiên là chữ THƯỜNG**
  (heading thật viết hoa đầu) VÀ (kết thúc bằng dấu câu HOẶC ≤3 từ, không có chữ hoa bên trong) → mảnh câu
  body MinerU tag nhầm ("ra.", "dùng bữa"). An toàn CJK/Thai (script không có case → không bao giờ dính).
- **Cover boilerplate**: text_level ở trang bìa (`page_idx ≤ 1`) không khớp TOC → tựa/tác giả/NXB/tagline,
  KHÔNG phải section. Bỏ (title level-0 vẫn tạo riêng từ `_document_title`). Section thật bắt đầu trang ≥2.
- **Chiều sâu/thứ tự** suy từ TOC hoặc `text_level`.
- **Chuẩn hoá tên UNICODE-aware** (`norm_title`): casefold + giữ chữ cái/số MỌI script (Unicode category
  `L*`/`N*`) + space, bỏ dấu câu. KHÔNG dùng `[^a-z0-9 ]` (xoá sạch tiếng Việt/CJK → hỏng match/dedup).
- **TOC-driven completion** (`_complete_from_toc`): body-block khớp NORM một mục printed-TOC → promote thành
  heading ở level TOC (parser per-trang đánh dấu không nhất quán; TOC là authority đầy đủ). Bắt số La Mã lẫn Ả-Rập.
- **Bất biến LEVEL (khóa chặt, `build_structural_index`)**: đúng 1 node level-0 = title; **level-1 CHỈ cho
  top-division** = mục TOC-level-1 (nếu có TOC) HOẶC chuỗi series phát hiện được (nếu không). Heading non-top nằm
  GIỮA hai top-division → **clamp ≥2** (không bao giờ đứng ngang chương); front/back matter ngoài range giữ level.
- **Parse TOC→level GENERIC (không hardcode ngôn ngữ)**: suy level từ ĐỊNH DẠNG (bold `**`/`#` = division;
  dòng plain không bao giờ L1; numeric prefix; indentation) — KHÔNG dựa nghĩa từ, KHÔNG regex "Chapter N".

**KHÔNG được**:
- Blocklist/allowlist **tên riêng** theo tài liệu ("Figure", "Table", "Note", "Chapter", "MLflow"…).
- **camelCase = code**: SAI — `GraphX`, `SparkR`, `DataFrame`, `MLflow`, `MLlib` là heading THẬT. Chỉ coi là
  code khi camelCase KÈM `,`/`()` (vd `distinct, dropDuplicates`).
- **Page-offset math** để dò chapter (TOC page ≠ physical page, offset thay đổi).
- **Font-size / bbox** để suy cấp (MinerU bbox không nhất quán).

## 4. Anti-pattern ĐÃ TỪNG SAI (đừng lặp lại)
- Dò "chapter" bằng offset trang giữa printed-TOC và body → sai vì offset không cố định (ch3/4/8 lệch).
- Regex running-header overfit một cuốn → sai cuốn khác. Thay bằng **mật độ trang** (generic).
- Ép mọi `text_level` thành heading → dính **figure caption / "Note" / code / bullet / index** (chính lỗi
  đang sửa). Thay bằng **TOC-authority + lọc typographic**.
- Thêm `#id` để ép tên duy nhất → tạo node rác `… #0/#1`. Thay bằng **gộp theo norm + phân biệt theo cha**.
- Chọn title = heading `text_level==1` đầu tiên → dính mảnh title bìa. Thay bằng **heading DÀI NHẤT ở trang
  ≤2** + gộp mảnh bìa vào level 0.

## 5. Khi thêm/sửa logic heading — checklist
1. Tín hiệu mới có **tổng quát** không? (thử tưởng tượng 1 cuốn khác ngôn ngữ/thể loại — còn đúng không?)
2. Có đang hardcode keyword/tên riêng không? Nếu có → tìm tín hiệu hình dạng/cấu trúc thay thế.
3. TOC-match có được giữ làm **lưới an toàn** để không bỏ nhầm heading thật không?
4. **Dry-test** trên content_list (MIỄN PHÍ, không gọi LLM): in danh sách heading GIỮ/BỎ, soi false-pos/neg,
   đặc biệt các tech heading có internal caps (GraphX/SparkR/DataFrame). Chỉ rebuild sau khi dry-test sạch.
