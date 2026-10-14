# Structural KG — trích xuất cây heading generic cho MỌI tài liệu (src/rag/)

File này mô tả **cách hệ thống hoạt động HIỆN TẠI**, không phải nhật ký sửa lỗi theo thời gian — mỗi lần
có thay đổi đáng kể, viết lại phần liên quan cho khớp trạng thái mới, không append thêm mục "[FIX ...]".
Lịch sử/lý do quyết định (nếu cần) nên tra qua `git log`/`git blame`, không lưu ở đây.

## Mục tiêu

Từ 1 tài liệu PDF bất kỳ, dựng 1 cây heading đa cấp (`level=0` = tiêu đề tài liệu, `1,2,3,…` = heading
nhỏ dần) để làm khung cho Knowledge Graph. **Không hardcode cho bất kỳ loại tài liệu/ngôn ngữ/nhà xuất
bản nào** — mọi tín hiệu dùng để suy luận cấp bậc đều generic (số thứ tự, thụt lề, in đậm, độ dài TOC,
nội dung riêng của từng node...). Mỗi node chỉ giữ lại nếu có nội dung thật của riêng nó — heading là
trang chia mục thuần tuý (không có chữ nào của riêng nó trước khi con nó bắt đầu) bị bỏ hẳn khỏi cây.

## Kiến trúc: 3 nguồn `toc_levels`, ưu tiên giảm dần

Hệ thống thử LẦN LƯỢT 3 nguồn để có `toc_levels` (`dict[title_chuẩn_hoá -> level]`), dùng nguồn ĐẦU TIÊN
cho kết quả khác rỗng:

1. **Outline/bookmark nhúng sẵn trong PDF** (`build_kg.py::_embedded_outline_levels`) — ưu tiên cao nhất.
2. **OCR trang TOC in sẵn + LLM suy level** (`build_kg.py::_toc_levels`) — khi không có outline nhúng.
3. **Không có gì cả** → rơi về pipeline family-detection thuần thân bài (không cần `toc_levels`).

Kiến trúc "TOC-based khi có metadata đáng tin, fallback suy luận khi không" được validate bởi
**[HiPS: Hierarchical PDF Segmentation of Textbooks](https://arxiv.org/pdf/2509.00909)** (Wehnert et al.,
2025) — họ benchmark trên 49 sách luật thật (9,812 heading), TOC-based đạt precision trung vị >0.9 khi
đầy đủ nhưng recall phụ thuộc TOC có bị thiếu entry hay không.

---

## Nguồn 1 (ưu tiên cao nhất) — Outline nhúng sẵn trong PDF

`build_kg.py::_embedded_outline_levels(pdf_path)`: đọc `fitz.open(pdf).get_toc()` — outline điều hướng
do nhà xuất bản/công cụ tạo PDF ghi thẳng vào file, **chính xác tuyệt đối, không qua OCR/LLM nào**. Đây
chính là nguồn "PDF outline metadata" mà HiPS xác nhận cho kết quả tốt nhất khi đầy đủ. Trong thực tế đa
số tài liệu test có outline nhúng (Hands-On ML: 360 entries/4 cấp; sherlock-holmes: 123 entries; think-
grow-rich: 205 entries; apache_spark: 265 entries) — chỉ 1 số ít không có (nhagiakim: 0), rơi về Nguồn 2.

- Ngưỡng chấp nhận: `len(toc) >= 3` — outline gần như luôn "có nhiều" hoặc "không có gì" rõ ràng, không
  cần ngưỡng phức tạp.
- Khoá bằng `_struct_norm` (= `structural._clean_chars`, xem mục "Chuẩn hoá text" bên dưới).
- Bỏ qua số trang của outline (dù đáng tin, offset 1-based→0-based đã verify đúng) — việc khớp vào thân
  bài vẫn dựa 100% vào `_toc_match` (text-only, xem Nguồn 2). Không sinh node theo trang.
- Không gọi LLM → không tốn tiền, không cần cache/version key.

---

## Nguồn 2 — OCR trang TOC in sẵn + LLM suy level

### Bước 1: Harvest TOC in sẵn — `ocr.py::_split_toc`
Với path OCR-cloud (VLM đọc từng trang), gom các dòng liên tiếp có dạng "tiêu đề … số trang" (≥12 dòng)
thành 1 khối, cắt ra khỏi content chính, ghi sidecar `{content_list}.printed_toc.txt`. Khi lùi 1 dòng để
bắt luôn dòng header đứng NGAY TRƯỚC (vd tiêu đề "Part I" không có số trang riêng), dùng `_looks_prose()`
(từ `structural.py`) để quyết định dòng đó có phải mục TOC thật hay không — không dùng ngưỡng đếm-từ thô.

### Bước 2: Suy level cho từng entry TOC — `build_kg.py`
- `_toc_levels()`: lấy TOC text (ưu tiên bản DÀI HƠN giữa `_extract_printed_toc()` heuristic và sidecar
  `.printed_toc.txt`), gửi LLM để dọn từng dòng + đoán level sơ bộ, rồi đưa qua `_robust_toc_levels()` để
  có level CUỐI CÙNG đáng tin hơn LLM tự đoán per-dòng (OCR bold/`#` không nhất quán giữa các trang).
- `_robust_toc_levels()`: theo thứ tự tín hiệu tin cậy giảm dần —
  1. **Family đánh số** (`_family_key`/`_numeric_families` từ `structural.py`, `min_members=2` — thấp
     hơn ngưỡng dùng cho thân bài vì TOC đã biết chắc là danh sách heading sạch): "Part I"/"Part II" hay
     "1."→"16." đều được nhận diện là 1 family nếu ≥2 lần xuất hiện theo cùng khuôn số/số La Mã.
  2. **Lồng nhau đa-family** (`_families_nesting` trong `structural.py`): thuật toán order-containment —
     family A lồng trong family B nếu MỌI vị trí thành viên A nằm trong khoảng vị trí thành viên B VÀ A
     có ít thành viên hơn. Family không có bằng chứng lồng với family nào khác bị LOẠI khỏi kết quả —
     không đoán bừa, entry đó rơi về tín hiệu (4) bên dưới thay vì bị ép level sai.
  3. **Thụt lề đo được** (`_measure_indent_levels`): chỉ dùng khi ≥2 mức thụt lề khác nhau, mỗi mức ≥3
     dòng (đủ số lượng để tin, tránh 1-2 dòng lệch tình cờ).
  4. **Level LLM tự đoán per-dòng** — fallback cuối cùng cho entry không rơi vào 3 tín hiệu trên.
- `_measure_indent_levels()`: đo độ thụt lề TRỰC TIẾP từ TOC text gốc (không qua LLM) — tín hiệu free,
  không nhiễu OCR, nhưng KHÔNG PHẢI mọi tài liệu đều có (nhiều TOC OCR ra flush-left, thụt lề = 0 hết).

### Bước 3: Khớp entry TOC vào heading thân bài — `structural.py`
`toc_levels` (dù từ Nguồn 1 hay Nguồn 2) chỉ là `dict[title_chuẩn_hoá -> level]` — phải khớp nó với ĐÚNG
đoạn text thân bài để cây KG có node thật (không chỉ tồn tại trong dict).

- `_toc_match(candidate, toc_keys)`: theo **[HiPS Appendix C.3](https://arxiv.org/pdf/2509.00909)**, thứ
  tự tin cậy giảm dần — (1) bằng nhau sau khi bỏ hết khoảng trắng, (2) chứa nhau (substring) sau khi bỏ
  khoảng trắng, (3) `rapidfuzz.fuzz.partial_ratio >= 80` (cùng thuật toán/ngưỡng HiPS dùng qua
  `fuzzywuzzy`). **Khác HiPS ở chỗ KHÔNG có tier fallback "trùng ≥1 từ dài ≥4 ký tự"**: HiPS dùng tier đó
  trong 1 lần quét CÓ GIỚI HẠN theo từng trang kỳ vọng (cursor theo trang), còn hệ thống này quét TOÀN
  VĂN BẢN không giới hạn trang — đã verify bằng dữ liệu thật: tier đó khiến 1 từ chung ngắn (vd "Learning")
  làm 1 heading nhỏ ở TRANG 35 cướp mất khoá TOC "Part II ... Deep Learning" trước khi scan tới đúng
  trang đích (trang 250) — nên đã bỏ hẳn tier này, chỉ giữ 3 tier đầu.
- `_complete_from_toc()`: quét các item CHƯA được gắn heading (`text_level` rỗng), thử khớp với
  `toc_levels` còn lại (mỗi entry TOC chỉ dùng 1 lần — bị `pop` khỏi `remaining` khi khớp, tránh 1 entry
  "ăn" nhiều đoạn thân bài khác nhau). Có filter loại câu văn dài (`_is_typographic_noise`) TRƯỚC khi thử
  fuzzy match — nếu không, câu văn tình cờ nhắc tới tên 1 chương/phần (vd "Part II, ... covers the
  following topics:" trong đoạn giới thiệu) có thể bị khớp NHẦM thay vì đoạn heading thật.
- `build_structural_index()`'s vòng override level: với node ĐÃ tồn tại (đã có `text_level` tag) nhưng
  title lệch OCR không khớp CHÍNH XÁC `toc_levels`, dùng cùng `_toc_match` + cùng filter noise. `top1`
  (biên "level 1" dùng để nâng level các node lạ nằm sau mốc top-level đầu tiên) được tính từ CHÍNH các
  khoá đã khớp. Chỉ cần `pos >= first_top` là đủ điều kiện nâng — KHÔNG có biên trên (một sách nhiều Part
  thì mọi chương sau Part CUỐI vẫn được nâng đúng, không chỉ chương giữa Part đầu và Part cuối).
- **KHÔNG sinh node giả (synthesize) cho TOC entry không khớp được** — quyết định có căn cứ từ chính
  HiPS: "Unmatched headings are logged for diagnostics but do not interrupt processing" — họ chấp nhận
  mất recall thay vì bịa cấu trúc. Lý do kỹ thuật riêng của hệ thống này: số trang IN trên TOC (OCR được)
  không map trực tiếp sang `page_idx` (chỉ số trang vật lý 0-based) vì front-matter thường đánh số La Mã
  riêng, offset không cố định — tự neo node giả theo số trang sai lệch sẽ tạo lỗi MỚI tệ hơn việc thiếu 1
  node. Số entry không khớp được in ra console (`[structural] N TOC entries have no matching body
  heading`) để chẩn đoán, không làm gián đoạn build. (Outline nhúng CÓ số trang chính xác — nhưng round
  này vẫn chưa dùng tới, xem "Giới hạn hiện tại".)

### Chuẩn hoá text dùng chung — điểm dễ gây bug nhất
- `norm_title(s)`: casefold + giữ ký tự chữ/số Unicode + gộp khoảng trắng, RỒI xoá tiền tố đánh số dạng
  "chapter/part/section N " (`_NUM_PREFIX`) — dùng cho HIỂN THỊ/dedup, nơi số thứ tự KHÔNG quan trọng
  cho việc coi 2 tiêu đề là "cùng 1 heading".
- `_clean_chars(s)`: y hệt `norm_title` nhưng KHÔNG xoá số thứ tự — dùng làm khoá join cho MỌI việc liên
  quan đến TOC-matching (khoá của `toc_levels` dù từ outline nhúng hay OCR-TOC, khoá so khớp trong
  `_toc_match`/`_complete_from_toc`/override loop) và cho `_family_key` (cần chính số thứ tự để nhận diện
  family).
- **Bài học thật đã gặp**: dùng `norm_title` làm khoá TOC từng khiến "Part I. The Fundamentals of Machine
  Learning" (có mô tả dài đi kèm → bị xoá sạch "part i", chỉ còn "the fundamentals of machine learning")
  và "PART I" (đứng một mình trên trang divider → KHÔNG bị xoá vì xoá hết sẽ thành rỗng, `norm_title` có
  fallback giữ nguyên) chuẩn hoá ra 2 chuỗi KHÔNG CÒN TỪ NÀO CHUNG — không thuật toán fuzzy nào cứu được
  vì chính dữ liệu gốc đã mất thông tin trước khi tới bước so khớp. Đây là lý do `_clean_chars` tồn tại
  tách riêng khỏi `norm_title`, và vì sao `_family_key` cũng dùng `_clean_chars` với lý do y hệt.

---

## Loại bỏ heading giả — không khớp TOC tin cậy + nội dung mỏng ("Constant width" case)

User chạy test Preface, thấy "Constant width"/"Constant width bold" (chỉ 1-2 dòng nội dung, VD định nghĩa
quy ước in đậm/nghiêng của O'Reilly) mỗi cái thành 1 slide riêng, và chỉ ra root cause: **đây còn không
phải heading thật**, bị OCR/parser gán nhầm `text_level` (đứng riêng dòng + font khác biệt — đúng kiểu lỗi
mà cả mục "Heading vs bold/italic thường" ở trên đã bàn). Điều tra xác nhận: outline nhúng sẵn của Hands-On
ML (321 entries, đáng tin cậy nhất) KHÔNG hề có "Constant width" — nhưng `build_structural_index()`'s vòng
đối chiếu TOC (trước đây) CHỈ dùng kết quả khớp để NÂNG level cho node không khớp, KHÔNG BAO GIỜ hạ/xoá 1
node chỉ vì nó không khớp được TOC, dù TOC đầy đủ đến đâu — đây chính là lỗ hổng khiến heading giả từ OCR
sống sót tới tận cây cuối cùng.

- **`structural.py::_prune_unmatched_leaves(nodes, per_item, items, toc_levels)`** (mới): bỏ qua nếu
  `toc_levels` rỗng hoặc `< _MIN_RELIABLE_TOC_ENTRIES = 20` (ngưỡng "đủ đáng tin để dùng làm căn cứ xoá" —
  tách biệt, cao hơn ngưỡng `len(toc) >= 3` dùng để CHẤP NHẬN outline nhúng, vì hành động ở đây là XOÁ chứ
  không chỉ nâng level). Với node LÁ (không con — có con thì không bao giờ xét, nhiều khả năng TOC-author
  chỉ bỏ sót chứ không phải giả): nếu `own_words < _MIN_UNMATCHED_WORDS = 60` VÀ chưa bị
  `_is_typographic_noise` bắt (tránh trùng việc với bộ lọc sẵn có) VÀ `_toc_match(title, toc_levels) is
  None` (tái dùng ĐÚNG hàm fuzzy-match sẵn có trong Nguồn 2 — không viết matcher mới) → prune, reattach nội
  dung riêng của nó lên cha gần nhất còn giữ lại.
- **Vị trí sửa — bắt buộc nằm trong `build_heading_tree()`, không phải `build_structural_index()`**:
  `build_heading_tree()` là điểm gọi DUY NHẤT dùng chung bởi CẢ 2 nhánh tiêu thụ cây heading — (a)
  `build_structural_index()` (sinh `structural_index.json`) VÀ (b) `build_kg.py::_structure_index()` (sinh
  marker `[[p|n]]` chèn vào chunk cho LLM, gọi `_rag_build_heading_tree` riêng). Sửa chỉ trong
  `build_structural_index()` (nơi vòng đối chiếu TOC cũ nằm) sẽ khiến 2 nhánh LỆCH NHAU — JSON nói node đã
  bị xoá nhưng marker vẫn chèn theo node cũ, gây lỗi tra cứu `retrieve_content.py::get_subtree_content` về
  sau. Đặt trong `build_heading_tree()` (nối tiếp ngay sau `_prune_contentless`, cùng cơ chế
  `_reattach_pruned()` tách ra dùng chung cho cả 2 hàm prune) đảm bảo tự động nhất quán ở CẢ 2 nhánh.
- **Verify thật đúng cách — dùng fuzzy `_toc_match`, KHÔNG dùng exact-match**: thử naive exact-`_clean_chars`
  matching trước (không qua `_toc_match`) trên toàn bộ cây thật của Hands-On ML cho kết quả NGUY HIỂM — 31
  node bị gắn cờ, gồm cả **"PART II" và chính node "Preface"** (lệch text do có mô tả dài kèm theo trong
  TOC, hoặc dot-leader rác dính vào title — chỉ fuzzy match mới nhận ra đúng). Đổi sang dùng `_toc_match`
  thật (hàm fuzzy có sẵn) → CHỈ còn 7 node bị gắn cờ, toàn bộ là false positive thật ("Constant width" +
  6 dòng comment code bị gán nhầm heading ở chương sau, vd "Select a linear model"/"Train the online DQN")
  — 0 node hợp lệ nào bị ảnh hưởng ("PART II"/"Preface"/"APPENDIX A-E" đều giữ nguyên đúng). Số liệu này là
  lý do BẮT BUỘC tái dùng `_toc_match`, không tự viết matcher đơn giản.
- **Verify thật rebuild KG** (`--force`, dùng cached parse — không có trang nào cần OCR lại vì per-page
  cache hit 100%): chi phí thật **142,660 token / 52 call** — ĐÚNG như kỳ vọng "gần miễn phí" (logic mới
  chỉ đổi node id trong `__struct__` metadata, KHÔNG đổi text gửi LLM entity-extraction, nên cache response
  hit gần hết — khác hẳn lần `--force` trước đó từng tốn ~4.6M token không rõ lý do). `build_heading_tree()`
  ra đúng 437 node (giảm 7 so với 444 trước khi có fix — khớp chính xác con số verify miễn phí ở trên);
  `structural_index.json` (437 + level-0 title) còn 438 heading node — "Constant width" biến mất,
  "Constant width bold", "PART II", "Preface" đều còn nguyên. Chạy lại `gen_slide(..., root_heading='Preface')`
  thật: 12 slide
  (giảm từ 13 — "Constant width" không còn là slide riêng), `lecture_title = "Preface"` vẫn sạch. Chi phí:
  4 call / 15,365 token.
- **Phát hiện phụ, CHƯA sửa** (user đã xác nhận scope round này): "Constant width bold" (239 từ, vượt
  ngưỡng 60 nên KHÔNG bị prune) — sở dĩ nhiều từ vì đang ôm nhầm nội dung của "Using Code Examples" (1
  entry TOC THẬT trong outline nhúng nhưng bị THIẾU hẳn node riêng — bug khác, sâu hơn, thuộc về
  `_complete_from_toc`/`_toc_match` không khớp được entry đó với đúng dòng thân bài, cần điều tra riêng).

## Bước cuối, áp dụng cho MỌI nguồn — bỏ node không có nội dung riêng

`structural.py::_prune_contentless(nodes, per_item, items)`, gọi ngay trong `build_heading_tree()` trước
khi trả về (không phải chỉ trong `build_structural_index()`, để cả `nodes` LẪN `per_item` — dùng cho
marker `[[p|n]]` chèn vào content thật đi vào LLM, xem `EXTRACT_PIPELINE.md` — luôn nhất quán với nhau).

Tiêu chí thuần NỘI DUNG, generic, không nhận diện theo tên/pattern ("Part"/"Phần" + số — dễ hardcode, dễ
sai): 1 node bị loại nếu (a) có ít nhất 1 con VÀ (b) lượng text thuộc RIÊNG nó (không tính con) dưới
ngưỡng nhỏ (`_MIN_OWN_WORDS = 15`). Ví dụ thật: "PART I" (trang chia, ~0 chữ trước khi "Chapter 1" bắt
đầu) bị loại; "Preface" (có đoạn văn thật trước "Roadmap") được giữ. Khi loại 1 node, con của nó nối lên
THẲNG cha của nó (không phải lên gốc tài liệu), rồi `level` được TÍNH LẠI từ độ sâu thật trong cây đã gọn
— không chốt cứng số cấp tối đa, tài liệu sâu 3-4 cấp thật vẫn giữ nguyên đúng độ sâu đó sau khi bỏ node
rỗng.

Cơ chế này áp dụng tự động cho CẢ node từ outline nhúng/OCR-TOC LẪN node cha giả do
`_synthesize_group_parents` sinh ra (Pipeline no-TOC bên dưới, vốn luôn rỗng nội dung vì là placeholder)
— không cần sửa riêng từng nguồn.

---

## Pipeline No-TOC — family-detection từ thân bài (khi không có toc_levels từ đâu cả)

Toàn bộ nằm trong `structural.py`:

- `_family_key(title)`: tách tiền tố chữ (lead) + có/không số thứ tự (Ả Rập hoặc La Mã) ngay sau đó.
  Dùng `_clean_chars`, KHÔNG dùng `norm_title` (lý do: mục "Chuẩn hoá text" ở trên).
- `_numeric_families(nodes, min_members=3)`: gom các heading cùng `lead` xuất hiện ≥3 lần thành 1
  "family" (vd mọi "CHAPTER N" trong sách) — ngưỡng 3 (cao hơn ngữ cảnh TOC) vì thân bài nhiễu hơn, cần
  nhiều bằng chứng hơn mới tin là 1 chuỗi đánh số thật.
- `_group_boundaries(members)`: phát hiện điểm SỐ THỨ TỰ BỊ RESET (vd ...12, 1, 2,... — báo hiệu 1 nhóm
  cấp cao hơn mới bắt đầu, ví dụ 1 "Phần"/"Truyện" mới trong tuyển tập nhiều truyện).
- `_synthesize_group_parents(nodes, items, fams)`: với mỗi family có ≥2 nhóm (có reset), sinh node cha
  cho mỗi ranh giới nhóm CHƯA có heading cấp cao hơn bao phủ — ưu tiên tìm title thật gần đó
  (`_nearby_title_candidate`, dừng ngay khi gặp dòng cùng family để không đi lạc), fallback placeholder
  trung thực (`"(chưa rõ tiêu đề, tr.N)"`) nếu không tìm được ứng viên an toàn — node placeholder này
  LUÔN bị `_prune_contentless` gỡ ở bước cuối (0 chữ nội dung riêng), con của nó tự nối lên đúng cấp cha.
  Family CHỈ 1 nhóm (không reset) → coi chính family đó là lớp cao nhất (`level=1`), đồng thời "demote"
  mọi heading lạ (không thuộc family) nằm GIỮA khoảng order của family lên tối thiểu `level=2`.

Trong thực tế, phần lớn tài liệu test có outline nhúng (Nguồn 1) nên pipeline này ít khi còn cần chạy tới
— chỉ áp dụng cho tài liệu KHÔNG có outline nhúng LẪN không harvest được TOC in sẵn (vd nhagiakim).

---

## Gom nội dung theo subtree heading — `src/rag/retrieve_content.py`

Cho slide generation: từ 1 heading A, lấy TOÀN BỘ nội dung của A và mọi con/cháu, **chỉ đúng phần thuộc
về A** — không lẫn nội dung heading khác dù có chung 1 concept được LLM trích (2 concept cùng tên ở 2 nơi
khác nhau trong tài liệu bị LightRAG GỘP THÀNH 1 NODE — nên **không** dùng graph quan hệ concept
(`graph_chunk_entity_relation.graphml`) để gom nội dung, chỉ dùng cây cấu trúc + text chunk gốc).

- `build_kg.py::build()` ghi thêm `structural_index.json` (từ `_build_structural_index()`'s kết quả) vào
  thư mục KB — để có cây cha-con ĐỘC LẬP, không cần parse lại graph (vô hướng, khó suy ngược chiều
  cha-con) hay chạy lại pipeline gốc.
- `chunking.py::marker_chunking_func`: mỗi chunk (~1200 token, KHÔNG cắt theo ranh giới heading — 1 chunk
  có thể trải qua nhiều heading) lưu `__struct__ = [[offset, page, node], ...]` — offset ký tự TƯƠNG ĐỐI
  trong chunk, một entry cho MỖI marker gốc (không dedupe, khác thiết kế cũ chỉ lưu `[page,node]` đã
  dedupe và mất offset). Marker `[[p|n]]` vốn được chèn vào đầu MỖI item gốc (không chỉ ở ranh giới
  heading) nên offset này đủ để tách chính xác từng đoạn thuộc về node nào, kể cả khi 2 heading chung 1
  chunk.
- `structural.py::_augment_with_structure` đọc `mk[2]` (node, thay vì `mk[1]` khi format còn 2 phần tử)
  — hành vi dựng cạnh `contains`/`mentions` KHÔNG đổi (vẫn tự dedupe qua `set` nội bộ hàm).
- `retrieve_content.py::get_subtree_content(kb_dir, root_id)`: với chunk format MỚI (3 phần tử), cắt
  `content[off_i:off_{i+1}]` cho từng marker liên tiếp — đoạn này thuộc CHÍNH XÁC node đó, chỉ giữ nếu
  node nằm trong subtree của A (`subtree_ids`, BFS theo `parent` pointer trong `structural_index.json`).
  Xử lý đúng cả trường hợp 1 chunk có phần thuộc A và phần thuộc heading Z không liên quan — loại đúng
  phần Z, giữ đúng phần A, không cần đoán/gộp. Với chunk format CŨ (KB build trước thay đổi này, chỉ có
  `[page,node]`) — fallback lấy CẢ chunk nếu có bất kỳ node nào trong subtree (lenient, có thể lẫn 1 đoạn
  nhỏ ở ranh giới), kèm cảnh báo console gợi ý `--force` rebuild để có ranh giới chính xác tuyệt đối.
- `find_heading_id(structural_index, title)`: tra heading theo tên, dùng lại `_toc_match`/`_clean_chars`
  (nhất quán với lớp matching TOC ở trên) thay vì viết cơ chế so khớp mới.

Đổi format `__struct__` KHÔNG làm mất cache LLM (chỉ đổi metadata đính kèm chunk, không đổi TEXT chunk gửi
LLM) — `--force` rebuild sau thay đổi này vẫn theo đúng thiết kế "rebuild gần-free" đã có.

`outline_md_from_structural_index(structural_index, root_id=None)`: sinh chuỗi outline dạng
`"#"*level + " " + title`, MỘT dòng/heading, đúng thứ tự `order` — `root_id=None` lấy cả cây, có `root_id`
thì chỉ lấy subtree (`subtree_ids`). Trả thêm `node_ids: list[int]` — id CHO TỪNG DÒNG theo ĐÚNG THỨ TỰ
(không phải `dict[title -> id]`) vì LLM ở bước sau được phép diễn giải lại (paraphrase) tiêu đề — so khớp
lại bằng TEXT sau đó không đáng tin, phải khớp THEO VỊ TRÍ (xem mục tích hợp workflow bên dưới).

---

## Tích hợp vào `src/workflow/` — PlanBuilder (outline) + SlidePacketBuilder (evidence)

Trước round này, `src/workflow/` (pipeline sinh slide chính) hoàn toàn tách biệt `src/rag/`. Đã nối theo
đúng vai trò từng agent (không đảo lộn kiến trúc có sẵn):

- **`PlanBuilderAgent`** (`src/workflow/agents/plan_builder.py`) — chỉ quyết định CẦN NÓI GÌ (outline).
  `build()` thử `_find_kb_dir(document_id)` trước — kiểm tra CẢ 2 quy ước đặt tên thư mục KB đang tồn tại
  song song trong dự án: `data/rag_kb/{document_id}` (bare, `extract.py` — CLI chính đang dùng thật) VÀ
  `data/rag_kb/{document_id}__{mode}...` (`build_kg.py`'s default riêng) — bug thật đã gặp lúc verify:
  chỉ check dạng có `__` khiến KB thật (dạng bare) không tìm thấy, âm thầm rơi về LLM đoán outline cũ mà
  KHÔNG báo lỗi gì (im lặng sai) — sửa xong, đã verify lại bằng KB thật. Chỉ nhận thư mục CÓ
  `structural_index.json`; nếu có, bỏ qua hẳn lệnh LLM đoán outline cũ, dùng
  `outline_md_from_structural_index()` (đủ ĐỘ SÂU THẬT, không giới hạn 4-8 heading như code gốc — hàng
  chục/trăm heading thật thì hàng chục/trăm slide thật). `root_heading` (tham số mới, optional) cho phép
  scope vào 1 subtree cụ thể thay vì cả sách; không khớp được tên thì fallback về cả cây kèm cảnh báo
  console (KHÔNG âm thầm coi như không có KG). Sau khi `_specify()` (LLM, KHÔNG đổi — vẫn viết
  `goal`/`slide_type`/table/formula như cũ) trả về `List[Slide]`, gắn `spec.kg_node_id = node_ids[i]`
  theo ĐÚNG VỊ TRÍ (không so tên) — dựa bằng chứng đọc code: mọi nhánh của `_specify()`
  (happy-path/`_fill_missing_specs`/`_fallback_specs`) đều trả về ĐÚNG `len(expected_titles)` phần tử,
  ĐÚNG thứ tự. Không có KB → `_build_outline()` cũ (LLM đoán 4-8 heading) chạy y nguyên, `kg_node_id` luôn
  `None`. Debug: khi dùng KG, ghi `outline_md` ra `kb_dir/generated_outline.md`.
- **`SlidePacketBuilderAgent`** (`src/workflow/agents/slide_packet_builder.py`) — quyết định EVIDENCE thật.
  `build_packets()`: nếu có KB VÀ có spec nào đó mang `kg_node_id` → `_build_packets_from_kg()`, THAY THẾ
  hẳn `_llm_map_pages()` (LLM cũ ĐOÁN trang nào khớp slide nào, dựa snippet 150 ký tự/trang — chính là chỗ
  yếu nhất trước đây) bằng tra cứu CHÍNH XÁC: mỗi spec có `kg_node_id` gọi thẳng `get_subtree_content()`.
  Heading CÓ con (tra qua `structural_index`'s parent pointers) → chỉ lấy đoạn `node == kg_node_id` (nội
  dung riêng, KHÔNG gồm subtree — tránh trùng với evidence riêng của slide-con, vì outline lấy đủ độ sâu
  nên cha VÀ con đều là slide riêng biệt); heading LÁ → full subtree (tự nhiên = nội dung riêng). Spec nào
  KHÔNG có `kg_node_id` (hiếm, vd lệch dữ liệu) vẫn map qua đường LLM cũ CHO RIÊNG slide đó (không rơi cả
  batch về path cũ). Không có KB/không spec nào có `kg_node_id` → `_build_packets_from_pages()` cũ chạy y
  nguyên. Đã gộp phần dựng packet-dict lặp lại ở 4 nhánh (kg/pages/pages-fallback/ablation1) thành 1 helper
  `_make_packet()`. Debug: ghi `kb_dir/generated_packets_debug.json` (slide_number/title/kg_node_id/
  evidence cho mọi slide).
- **`Slide`** (`src/models/slide.py`): thêm `kg_node_id: Optional[int] = None` — cầu nối 2 agent trên,
  optional/có default nên không phá chỗ nào đang tạo `Slide` theo cách cũ.
- Contract output của CẢ 2 agent (`{"lecture_title", "slide_specs"}` và packet dict shape) **không đổi** —
  `content_quality.py`/`direct_bullet_writer.py`/`graph.py` không cần sửa gì.

**`root_heading` đã nối xuyên suốt pipeline thật** (không còn "chưa làm" — xem mục kế tiếp).

---

## `main.py` tự động build KG cho tài liệu dài + `root_heading` xuyên pipeline thật

Trước round này, build KG chỉ chạy tay qua `src/rag/extract.py` — pipeline thật (`main.py::gen_slide()`)
không bao giờ tự tạo/dùng KG. Giờ đã nối THẲNG vào flow chuẩn, không cần thao tác tay:

- `main.py::gen_slide()`: ngay khi có `document_path` (trước `extract_file`), đo số trang bằng
  `fitz.open(document_path).page_count` (free, không LLM). Nếu **>20 trang**
  (`KG_AUTO_BUILD_PAGE_THRESHOLD`, hardcode có chủ đích, không đưa thành config/CLI round này) → gọi
  `src.rag.extract.extract_one(pdf=Path(document_path), embed="local", do_view=False, kb_suffix="",
  force=False, pages=None, window=40)` (bọc `asyncio.run`). Tài liệu ≤20 trang bỏ qua bước này hoàn toàn
  — hành vi cũ giữ nguyên. `extract_one()` đã có sẵn cơ chế skip-nếu-đã-build
  (`if graphml.exists() and not force`) nên gọi lại cho KG đã tồn tại (vd Hands-On ML) **miễn phí thật**
  — verify bằng cách gọi trực tiếp, log ra đúng `[skip] KG already exists`, không có dòng `llm summary`
  nào phát sinh (không LLM call nào chạy).
- `root_heading: str | None = None` nối xuyên suốt: `main.py::gen_slide()` (+ CLI `--root_heading`, chỉ
  áp dụng cho chạy 1 file đơn — không áp dụng cho batch cả thư mục vì mỗi tài liệu cần root khác nhau) →
  `preprocess_context()` (tham số mới, đưa vào `initial_state['root_heading']`) →
  `WorkflowState.root_heading` (`src/workflow/state.py`) → `graph.py::plan_builder_node` đọc
  `state.get('root_heading')`, truyền `plan_builder.build(context, root_heading=...)` (tham số này
  `PlanBuilderAgent.build()` đã có sẵn từ round trước, chỉ thiếu người gọi thật).
- **Flow chuẩn (không truyền `root_heading`) = dùng CẢ cây KG** — không giới hạn 4-8 slide, sách có bao
  nhiêu heading thật thì ra bấy nhiêu slide (đã đúng từ round tích hợp trước, không cần sửa gì thêm).
- **Verify thật** (không chỉ mock): tạo PDF cắt 8 trang (Preface, page_idx 14-21 của Hands-On ML gốc,
  ĐẶT CÙNG TÊN STEM nhưng ở thư mục khác `data/raw/` để không đè file gốc — `document_id` khớp theo
  `Path(document_path).stem`, không phụ thuộc thư mục chứa), gọi `gen_slide(..., root_heading='Preface')`
  thật — 8 trang < 20 nên KHÔNG kích hoạt auto-build (KG đã có sẵn), đúng nhánh cần verify: dùng KG có
  sẵn + scope đúng subtree + lưu thật vào `data/context/`, `data/assets/`, `data/lectures/`. Kết quả:
  13 slide (đúng bằng số node con thật của heading "Preface" trong `structural_index.json`, không bị
  chặn ở 4-8), lecture title = đúng title (dính rác OCR-TOC "Preface ... xiii" — xem giới hạn dưới, KHÔNG
  phải bug mới của round này), chi phí thật nhỏ: 5 LLM call / 16,375 token.
- **Chưa làm/ngoài phạm vi round này**: không tối ưu chi phí Phase 1-3 (`extract_file`/VLM extraction) cho
  tài liệu dài — đây là hệ thống trích xuất RIÊNG của `main.py`, KHÔNG được rẻ đi nhờ có KG (build KG chỉ
  ảnh hưởng Phase 2's PlanBuilder/PacketBuilder); `effective_lecture_id`/cache "skip nếu đã có" chưa phân
  biệt theo `root_heading` (2 lần chạy cùng `document_id` khác `root_heading` có thể đụng cache lẫn nhau
  — chưa xử lý); ngưỡng "20 trang" hardcode, chưa thành config.

---

## Root heading không tự thành slide + 1 heading có thể ra nhiều slide

Verify thật `root_heading='Preface'` (round trước) cho thấy "Preface" (root của scope) bị biến thành 1
SLIDE riêng NGOÀI 12 slide con — sai ý: khi scope vào 1 heading, heading đó phải là **tiêu đề chính của
deck** (giống `title_id` — tiêu đề cả tài liệu — vốn đã luôn bị loại khỏi outline), không phải 1 slide.
Đồng thời bỏ giới hạn "1 heading = đúng 1 slide": heading nào evidence dồi dào được phép ra nhiều slide
hơn (tối đa 4/heading), **để LLM tự quyết định lúc viết spec** (không tính trước theo số từ — lựa chọn
của user qua AskUserQuestion).

- **`retrieve_content.py::outline_md_from_structural_index`**: khi `root_id` có ≥1 con thật, loại chính
  `root_id` khỏi outline (chỉ còn heading con) — giống hệt cách `title_id` đã luôn bị loại. Nếu `root_id`
  là heading LÁ (không con) thì GIỮ nguyên nó làm dòng outline duy nhất (tránh outline rỗng — mất hết nội
  dung). Đổi return type thêm `root_excluded: bool` để caller biết có cần lấy `lecture_title` từ chính
  root hay không.
- **`plan_builder.py::_try_kg_outline`**: khi `root_excluded=True`, lấy thẳng title của root heading từ
  `structural_index`, làm sạch qua `_clean_display_title()` (strip đuôi dot-leader + số trang còn sót từ
  TOC-harvest, vd `"Preface ........ xiii"` → `"Preface"` — CHỈ áp dụng cho hiển thị `lecture_title`,
  KHÔNG sửa dữ liệu gốc trong KG) làm `root_title_override`; `build()` dùng override này thay
  `_generate_title()` (vốn lấy dòng ĐẦU TIÊN outline — sau khi loại root, dòng đầu sẽ là heading con đầu
  tiên, sai ý nếu không override).
- **1 heading → tối đa 4 slide** (`MAX_SPLIT_PER_HEADING = 4`): prompt trong `_build_spec_prompt` đổi từ
  "mỗi heading ĐÚNG 1 object" thành "ÍT NHẤT 1, TỐI ĐA 4 nếu evidence đủ phong phú — mỗi phần `goal` khác
  biệt, không trùng nội dung, cùng chung tiền tố số thứ tự outline". Cơ chế align lại VIẾT LẠI theo GOM
  NHÓM THEO TIỀN TỐ SỐ (thay vì khớp 1-1 theo vị trí như trước — bug thật: vị trí lệch ngay khi 1 heading
  trả >1 object): `_group_specs_by_prefix()` (hàm mới, dùng chung) gom `raw_specs` theo tiền tố số đã trích
  (regex `^([\d.]+)`, đã có sẵn từ trước), cắt còn tối đa 4/nhóm. `_align_specs_to_outline`,
  `_find_missing_titles` (chỉ cần đổi sang dùng `_prefix_of()` dùng chung, KHÔNG cần đổi logic — vốn đã
  chỉ kiểm tra "tiền tố có xuất hiện hay không", tự nhiên đúng với gom-nhóm), `_fill_missing_specs` (bug đã
  sửa: bản cũ dùng `dict` theo tiền tố nên chỉ giữ ĐÚNG 1 spec/tiền tố dù nhóm có 3 — mất hẳn phần tách ở
  đường lỗi/repair; bản mới giữ NGUYÊN cả nhóm, chỉ chèn đúng 1 fallback cho heading thật sự thiếu). Cả 3
  hàm giờ trả thêm `group_sizes: List[int]` (song song `expected_titles`) — `_specify()` trả
  `(specs, group_sizes)`. `build()`'s nhánh KG: expand `node_ids` theo `group_sizes` TRƯỚC khi gán
  `spec.kg_node_id` (N slide cùng 1 heading đều nhận đúng `kg_node_id` của heading đó).
- **KHÔNG cần sửa** `SlidePacketBuilderAgent`/`DirectBulletWriterAgent`/`graph.py`: `_build_packets_from_kg`
  xử lý độc lập theo từng spec nên N slide cùng `kg_node_id` tự nhiên nhận cùng 1 khối evidence gốc của
  heading đó (không chia nhỏ evidence theo yêu cầu — user chọn để LLM tự quyết định, không chọn cơ chế
  chia theo độ dài); rủi ro trùng nội dung giữa các slide cùng heading giảm nhờ 2 cơ chế SẴN CÓ không cần
  code thêm: `goal` khác nhau do LLM viết riêng mỗi phần, và `direct_bullet_writer.py`'s prompt đã có sẵn
  chỉ dẫn "avoid repeating facts across neighboring slides" (các slide cùng heading luôn cạnh nhau, nhiều
  khả năng cùng 1 batch `BATCH_SIZE=4`).
- **Verify thật** (Preface PDF, sau khi xoá lecture output cũ để bỏ qua cache "skip nếu đã có"): 12 slide
  (giảm từ 13 — không còn slide "Preface" riêng), mỗi `kg_node_id` xuất hiện ĐÚNG 1 lần trong
  `_plan_spec.json` (không heading nào bị tách — evidence mỗi heading con của tài liệu test này khá mỏng,
  ~1 trang/heading, đúng như dự đoán trong plan — cơ chế tách CHỈ verify được qua unit test mock, chưa
  quan sát được trong dữ liệu thật này), `lecture_title = "Preface"` (sạch, hết dấu chấm/số trang). Chi
  phí: 4 LLM call / 15,065 token.

## Outline.md có level + không lặp heading khi bị tách slide — `preprocessing_context.py::_outline_lines`

`..._outline.md` (artifact chính thức lưu cùng lecture, KHÁC với `generated_outline.md` debug của KG) đã
qua 2 vòng sửa, giờ gộp chung vào 1 hàm `_outline_lines(document_id, specs)` (thay cho `_outline_levels`
cũ, đã bị xoá — hàm mới trả `(title, level)` thay vì chỉ `level`):

- **Có level thật** (vòng 1): nếu spec mang `kg_node_id`, tra `level` thật qua `structural_index.json` (tái
  dùng `PlanBuilderAgent._find_kb_dir` + `retrieve_content.load_structural_index`), **rebase** sao cho
  heading NÔNG NHẤT thật sự xuất hiện trong deck này = level 1 (vd deck scope `root_heading='Preface'` có
  heading con vốn level=2 trong cây CẢ tài liệu → rebase về level=1, tránh `##` vô nghĩa khi không có `#`
  làm gốc). Verify thật: outline Preface ra toàn `#` phẳng — ĐÚNG theo dữ liệu (12 heading con của
  "Preface" đều `level=2` ngang hàng thật trong KG, không phải bug rebase).
- **Không lặp heading khi 1 heading → nhiều slide** (vòng 2, sau khi tính năng "1 heading → tối đa 4 slide"
  ở mục trên bắt đầu tách slide thật): outline cũ lặp qua `final_specs` (1 phần tử/SLIDE, đã flatten) nên 1
  heading bị tách 3 (vd "Roadmap") in ra outline 3 LẦN với hậu tố LLM tự thêm ("Roadmap: Part I"/"Part
  II"/"Guidance on Sequence") — user: "outline chỉ là tên của cái heading đó thôi". Sửa: dedupe
  `final_specs` LIÊN TIẾP theo `spec.kg_node_id` (chỉ gộp khi liền kề — các slide cùng 1 heading LUÔN đứng
  cạnh nhau vì cùng đến từ 1 outline-line gốc, xem `MAX_SPLIT_PER_HEADING`), lấy tên hiển thị từ
  `structural_index["headings"][id]["title"]` (qua `_clean_display_title()`, KHÔNG lấy `slide_title` — đã
  bị LLM diễn giải/thêm hậu tố phần tách). Spec không có `kg_node_id` (nhánh không-KG) KHÔNG dedupe — không
  có tín hiệu nhóm nào đáng tin. Verify: unit test 4 case (heading không tách → 1 dòng như cũ; tách 3 → gộp
  còn 1 dòng tên sạch; 2 heading khác nhau liền kề không gộp nhầm; nhánh không-KG giữ nguyên). Verify thật:
  outline Preface hết lặp (11 dòng = đúng 11 heading con thật của Preface).
- **Tiêu chí "khi nào nên tách" cũng được siết lại** trong `plan_builder.py::_build_spec_prompt` cùng round
  này — phát hiện qua so evidence thô với bullet thật (deck Preface trước đó): case "Roadmap" tách 3 slide
  hợp lý (evidence có ranh giới cấu trúc thật "Part I"/"Part II"/lời khuyên thứ tự học, evidence GIỐNG HỆT
  cho cả 3 — 1956 ký tự, đúng thiết kế "không chia nhỏ evidence, để LLM tự đọc"), nhưng case "Machine
  Learning in Your Projects" tách 2 slide KHÔNG hợp lý (evidence chỉ là đoạn văn dẫn nhập kể chuyện không
  ranh giới rõ, và 1 câu FOOTNOTE bị nâng lên ngang hàng nội dung chính ở 1 trong 2 slide). Sửa prompt: chỉ
  tách khi evidence có ≥2 tiểu chủ đề THẬT SỰ tách biệt (ranh giới cấu trúc rõ, không phải chỉ dài dòng),
  dặn rõ bỏ qua footnote/chú thích khi cân nhắc tách VÀ khi viết bullet. Verify thật (1 lần chạy lại
  Preface): case xấu ("ML in Your Projects") không còn tách — ĐÚNG hướng; nhưng lần này case tốt
  ("Roadmap") CŨNG không tách (trước đó có tách) — có thể do prompt mới hơi thận trọng quá tay, cũng có thể
  do nhiễu ngẫu nhiên giữa các lần gọi (temperature=0.3) — CHỈ 1 mẫu, chưa đủ để kết luận chắc, cần quan
  sát thêm qua nhiều lần chạy/tài liệu khác nếu muốn tinh chỉnh lại ngưỡng.
- **Thử nghiệm bỏ cap ký tự bullet** (theo yêu cầu user, KHÔNG phải thay đổi code chính thức — chạy script
  tạm, không sửa `direct_bullet_writer.py`): gọi lại `DirectBulletWriterAgent` thật trên đúng packet Preface
  đã có, xoá hẳn câu giới hạn ký tự khỏi prompt + bỏ qua bước `_compact_slide_bullets` hậu xử lý. Kết quả:
  bullet TỰ NHIÊN dài **132-223 ký tự** — GẦN NHƯ TRÙNG với cap 220 hiện có (đặt ở round trước) — tức cap
  220 đã khá sát độ dài "tự nhiên" của model cho loại nội dung này, không cần nới thêm. Cũng quan sát được:
  bullet lấy từ footnote (vấn đề ở case "ML in Your Projects" trên) KHÔNG còn xuất hiện trong lần chạy có
  chỉ dẫn "bỏ qua footnote" mới — xác nhận chỉ dẫn mới có tác dụng cả ở bước VIẾT bullet, không chỉ bước
  quyết định tách.

---

## Heading vs bold/italic thường — dùng font metadata thật của fitz làm gợi ý cho VLM OCR

User hỏi có cách nhận diện đậm/nghiêng thông minh hơn để bắt heading chính xác hơn, đúng lo ngại: có chữ
đậm/nghiêng KHÔNG PHẢI heading (nhấn mạnh giữa câu, thuật ngữ, caption). Trước khi code, đã research +
verify thật, đổi hẳn hướng ban đầu (chỉ sửa prompt VLM tự đoán):

- **Research**: paper *"Reading ≠ Seeing: Diagnosing and Closing the Typography Gap in Vision-Language
  Models"* (2026, test 15 VLM hàng đầu) — nhận diện font STYLE (đậm/nghiêng) qua thị giác thuần: model tốt
  nhất chỉ 33.6% accuracy, đa số đoán "regular" bất kể đúng sai (70.5% mẫu ĐẬM THẬT bị đoán nhầm). Model to
  hơn KHÔNG giúp (thiếu dữ liệu train, không phải giới hạn năng lực); fine-tune cũng KHÔNG sửa được style
  riêng (trong khi size/color sửa được) — có thể do giới hạn kiến trúc patch-based vision encoder. Nhưng
  VLM **đọc TEXT gần như hoàn hảo** — điểm yếu CHỈ ở STYLE. Kết luận: sửa prompt để VLM tự đoán đậm/nghiêng
  qua ảnh có TRẦN THẤP, không sửa được bằng prompt.
- **Verify thật** (đọc trực tiếp `data/raw/nhagiakim.pdf` bằng `fitz.Page.get_text("dict")` — đúng cuốn có
  lỗi ToUnicode gây lỗi dấu, vd "à"→"{"): xác nhận CÓ mojibake thật ("đáp"→"đ|p"), NHƯNG bbox/font
  name/size/flags của từng span vẫn NGUYÊN VẸN — vì đến từ glyph-rendering pipeline, tách biệt hoàn toàn
  với bảng ToUnicode (chỉ ảnh hưởng decode ký tự). `fitz` cho tín hiệu đậm/nghiêng CHÍNH XÁC ngay cả trên
  chính tài liệu có lỗi encoding mà OCR-cloud được thêm vào để giải quyết.

**Thiết kế cuối — chia đúng việc cho đúng hệ thống**: `fitz` (free, cục bộ) đọc font metadata THẬT làm
GỢI Ý, VLM (đã đang gọi, không tốn thêm lần gọi) vẫn là bên đọc chữ + quyết định heading cuối cùng (có
ảnh thật, biết phân biệt heading vs nhấn mạnh giữa câu — thứ fitz's metadata thuần không biết).

- `src/rag/ocr.py::_style_candidates(page)`: đọc `page.get_text("dict")["blocks"]`, gom theo DÒNG (chỉ
  nhận `len(block["lines"]) == 1` — cả block chỉ có 1 dòng, tức KHÔNG nằm trong đoạn văn dài hơn), lọc
  ngắn (≤12 từ, không kết thúc bằng dấu câu). Đậm/nghiêng qua `_is_bold_span`/`_is_italic_span`: ưu tiên
  font NAME chứa "bold"/"italic"/"oblique" (đáng tin hơn), phụ thêm `span["flags"]` bitmask (bit 16=bold,
  bit 2=italic theo PyMuPDF). Lọc thêm: bỏ dòng thuần số (`text.isdigit()` — page-number artifact) và
  caption pattern (`_CAPTION_RE`: "Figure/Table/Equation/Algorithm/Listing/Chart/Example N" — cùng
  vocabulary với `direct_bullet_writer.py::_clean_evidence_text`'s caption-stripping regex).
  **Phát hiện thật khi verify** (không đoán): KHÔNG có 2 filter này, ~90% trang của Hands-On ML bị flag —
  chủ yếu do caption ảnh/công thức in nghiêng (`"Figure 1-1. The traditional approach"`) và số trang in
  đậm đứng lẻ (`"3"`) — cả 2 ĐẬM/NGHIÊNG THẬT nhưng chắc chắn không phải heading; thêm 2 filter đưa tỷ lệ
  xuống ~56% (14/25 trang), phần còn lại verify thật là subheading thật (vd "Main Challenges of Machine
  Learning", "Insufficient Quantity of Training Data") — kèm 1 loại nhiễu còn sót chấp nhận được (danh sách
  liên hệ in nghiêng "Find us on Facebook: ...", công thức toán in nghiêng không có prefix "Equation N.") —
  không lọc thêm vì đây CHỈ LÀ GỢI Ý, VLM tự loại khi nhìn ảnh thật (đã verify — xem dưới).
- `ocr_pdf()`: tại chỗ tính pixmap mỗi trang cần OCR, gọi thêm `_style_hint(_style_candidates(d[i]))`,
  truyền vào `_ocr_cloud_page(png, hint)` — nối vào cuối `_OCR_PROMPT` khi non-empty, trang không có
  candidate giữ nguyên prompt cũ (tránh nhiễu). Nhánh backend local (paddle) không dùng hint, chỉ unpack
  bỏ qua phần tử thứ 3 của `todo`.
- **KHÔNG dùng ink-density/Stroke Width Transform** (pixel-based) như hướng research ban đầu — font
  NAME + `flags` đã đủ tin cậy, RẺ hơn nhiều (không cần crop/threshold ảnh dù numpy/PIL đã có sẵn qua
  `_ocr_local_md`), và cho CẢ đậm LẪN nghiêng (ink-density chỉ đo được đậm, không đo được nghiêng).

**Verify thật OCR-cloud** (gọi VLM thật, 3 trang, có/không hint): KHÔNG có trang nào bị hồi quy (đậm/nghiêng
giữa câu — danh sách liên hệ, công thức toán — vẫn giữ plain text ở CẢ 2 trường hợp). Nhưng CŨNG không có
trang nào cho thấy hint THAY ĐỔI kết quả — 2/3 trang test (trang 20, 43) VLM đã tự nhận đúng heading ngay
cả KHÔNG có hint (đủ to/đậm rõ ràng để baseline VLM bắt được); trang thứ 3 (trang 42) không có candidate
nào (các "heading" cấp 4 trong KG ở trang đó thực ra là comment trong code block, không phải text đậm/
nghiêng đứng riêng). **Kết luận trung thực**: đã verify AN TOÀN (không gây hồi quy), CHƯA verify được cải
thiện thật trên mẫu này — Hands-On ML's heading đều đủ rõ ràng để baseline VLM đã đúng sẵn. Giá trị thật
của cơ chế này nhiều khả năng rõ hơn trên tài liệu có heading tinh tế hơn (chỉ đậm, không khác size) hoặc
chất lượng ảnh kém hơn — chưa có mẫu như vậy để verify trong round này.

## Nghiên cứu paper — áp dụng được gì

- **[A Supervised Learning Approach For Heading Detection](https://arxiv.org/pdf/1809.01477)**
  (Budhiraja & Mago, 2018): Decision Tree phân loại NHỊ PHÂN heading/không-heading (96.95% accuracy),
  feature mạnh nhất là **Bold** (Pearson r=0.70) và **Font Threshold Flag** (font-size lớn hơn font phổ
  biến nhất — tính riêng mỗi tài liệu, hoàn toàn generic). **Không có cơ chế phân cấp (level/hierarchy)**
  — tác giả xác nhận đây là "future work" chưa làm, nên áp dụng trực tiếp cho bài toán hiện tại (vốn CHÍNH
  LÀ vấn đề cấp bậc) rất hạn chế. Ý tưởng font-threshold (tần suất font phổ biến nhất/tài liệu) là 1 tín
  hiệu free, độc lập OCR/LLM, đáng cân nhắc SAU NÀY cho path `--parser marker/mineru` (có font-size thật
  qua PyMuPDF) — CHƯA triển khai, không thuộc phạm vi round này.
- **[HiPS: Hierarchical PDF Segmentation of Textbooks](https://arxiv.org/pdf/2509.00909)** (Wehnert et
  al., 2025): nguồn chính cho việc ưu tiên outline nhúng PDF (Nguồn 1) và thiết kế `_toc_match` (Nguồn 2,
  Appendix C.3), cũng như quyết định KHÔNG sinh node giả cho TOC entry không khớp được. Cho thấy các
  parser phổ biến (Docling, GROBID, Marker ở chế độ mặc định) thường LÀM PHẲNG cấp bậc trong benchmark của
  họ — xác nhận việc tự xây hệ thống cấp bậc riêng (thay vì chỉ dựa `text_level` gốc của parser) là cần
  thiết, không phải công sức thừa.
- **"Reading ≠ Seeing: Diagnosing and Closing the Typography Gap in Vision-Language Models"** (2026, test
  15 VLM hàng đầu): nhận diện font STYLE (đậm/nghiêng) qua thị giác thuần — model tốt nhất chỉ 33.6%
  accuracy, model to hơn KHÔNG giúp (thiếu dữ liệu train, không phải giới hạn năng lực), fine-tune cũng
  KHÔNG sửa được style riêng (trong khi size/color sửa được). Lý do trực tiếp round "heading vs bold/italic"
  (mục trên) đổi từ "sửa prompt để VLM tự đoán" sang "dùng font metadata thật của `fitz` làm gợi ý" — VLM
  đọc TEXT gần như hoàn hảo (điểm mạnh), chỉ STYLE là điểm yếu, nên chia đúng việc cho đúng hệ thống thay
  vì cố sửa điểm yếu bằng prompt engineering (theo paper, có trần thấp, không sửa được bằng prompt).

---

## Giới hạn hiện tại (nói thẳng, không hứa suông 100%)

- Nếu TOC bị OCR/LLM làm rớt hẳn 1 dòng trước khi tới bước matching thì KHÔNG cơ chế nào cứu được (không
  áp dụng khi có outline nhúng — outline không qua OCR/LLM nên không có rủi ro này).
- Không sinh node cho TOC entry không tìm được heading thân bài tương ứng — kể cả khi nguồn là outline
  nhúng (có số trang chính xác, offset -1 đã verify đúng, đủ an toàn để làm việc này) — CHƯA làm trong
  round này, giữ nguyên quyết định cũ để không mở rộng phạm vi ngoài bug đang sửa.
- `_prune_contentless` chỉ nhìn thấy cấu trúc cây TẠI THỜI ĐIỂM `build_heading_tree()` chạy — với node có
  `text_level` NATIVE bằng đúng level của node cha (trước khi bước override theo TOC sửa lại), 2 node đó
  tạm thời là ANH EM (không phải cha-con) nên node cha có thể "trông như không có con" và KHÔNG bị gỡ dù
  đáng lẽ nên gỡ (vd "PART I"/"PART II" của Hands-On ML vẫn còn tồn tại làm node gần-rỗng, dù con thật của
  chúng — Chapter 1-16 — vẫn được nối đúng cấp sau bước override). Không ảnh hưởng tính đầy đủ/đúng thứ tự
  của nội dung (marker `[[p|n]]` chỉ cần ID node ổn định, level/parent đúng lấy từ `structural_index` ở
  bước build graph sau) — chỉ để sót lại vài node gần-rỗng vô hại. Sửa triệt để cần chuyển bước override
  theo TOC vào bên trong `build_heading_tree()` (thay vì `build_structural_index()` riêng) — không làm
  trong round này vì phạm vi rộng hơn hẳn bug gốc, và không ảnh hưởng nội dung.
- `_family_key`'s phát hiện số La Mã chỉ nhận `i,v,x,l,c,d,m` chuẩn — không bắt các biến thể ký hiệu số
  khác (chữ số Trung/Nhật, ký hiệu chương kiểu khác) — cần mở rộng `_ROMAN_RE`/`_is_enum` nếu gặp.
- KB build TRƯỚC khi đổi format `__struct__` (xem mục "Gom nội dung theo subtree heading") chỉ có
  `structural_index.json`/`__struct__` cũ hoặc KHÔNG có `structural_index.json` — `get_subtree_content`
  sẽ lỗi thiếu file hoặc rơi về fallback lenient cho tới khi `--force` rebuild lại KB đó.
- **`--force` rebuild KHÔNG LUÔN "gần miễn phí"**: verify thật cho Hands-On ML tốn ~4.6M token thật (cả
  gpt-4o-mini trích entity LẪN gpt-4.1-mini caption ảnh chạy lại 100% — cache KHÔNG hit ở cả 2 phía), dù
  thiết kế cache lẽ ra phải giữ được (đổi `__struct__` chỉ đổi metadata, không đổi text chunk gửi LLM).
  Chưa xác định chắc chắn nguyên nhân — khả năng cao nhất: đây là lần ĐẦU sách này build full-multimodal
  trong phiên làm việc, nên chưa có cache thật để tái dùng (không phải do thay đổi lần này gây cache-miss).
  Trước khi hứa "gần miễn phí" với user, nên kiểm tra `kv_store_llm_response_cache.json`/`vision_cache.json`
  đã có dữ liệu cho ĐÚNG tài liệu đó chưa, không giả định.
- Title 1 số heading còn dính rác OCR/TOC-harvest (vd "Preface" ra thành "Preface ..... xiii" — dot-leader
  + số trang lẫn vào text heading thật) — bug dữ liệu có sẵn từ trước, phát hiện lại khi verify tích hợp
  workflow (lan sang cả `lecture_title` khi heading đó được chọn làm gốc) — chưa sửa, ngoài phạm vi round
  workflow-integration này (thuộc lớp harvest ở `ocr.py`/pipeline OCR-cloud, không phải lớp cây/retrieval).
