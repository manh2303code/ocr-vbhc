# Hướng dẫn phát triển OCR VBHC

Ứng dụng desktop **chạy hoàn toàn offline** để số hóa, tra cứu và hỏi đáp văn
bản hành chính. Toàn bộ pipeline — OCR, embedding, rerank, sinh câu trả lời —
thực thi tại chỗ; không có network call nào lúc runtime.

```
PDF/ảnh → Chandra OCR 2 (llama.cpp) → parser HTML → 12 trường ND30
                                                  ↓
                          embedding tiếng Việt + ViRanker + sqlite-vec
                                                  ↓
                              trợ lý RAG (Qwen3) trả lời kèm citation [n]
```

Hướng dẫn triển khai và vận hành cho người dùng cuối nằm ở
[PORTABLE_README.md](../PORTABLE_README.md). Tài liệu này dành cho người phát triển.

## Yêu cầu

| Thành phần | Phiên bản |
|---|---|
| Python | 3.9 (runtime nhúng trong `runtime/`) |
| Qt | PyQt5 5.15.11 + PyQtWebEngine 5.15.7 |
| OCR | Chandra OCR 2 Q4_K_M qua llama.cpp (port 9999) |
| Chat | Qwen3 4B/8B Q4_K_M qua llama.cpp (port 9998) |
| Hệ điều hành | Windows 10/11 x64 |

Các thư mục nặng — `runtime/`, `gguf/`, `model_embedding/`, `model_reranker/`,
`bin/`, `tools/poppler/` — **không nằm trong Git**. Repo chỉ chứa mã nguồn và
tài nguyên giao diện. Xem `build_portable.py` để dựng lại package đầy đủ.

## Bản đồ module

**Khởi động và vỏ ứng dụng**
- `main.py` — điểm vào, nạp font, tạo `AppController`
- `startup_controller.py` — điều phối landing nhẹ ↔ workspace nặng
- `landing_page.py` / `landing_data.py` — màn hình bắt đầu (WebEngine, có fallback Qt thuần)
- `frontend/landing/` — HTML/CSS/JS của landing, chỉ nạp tài nguyên cục bộ

**Giao diện nghiệp vụ**
- `main_window_1.py` — cửa sổ chính, editor, PDF viewer
- `ui_commands.py` — danh mục lệnh duy nhất cho toolbar, command palette và bảng phím tắt
- `workspace_layout.py` — quy tắc chia cột thuần hàm, có test riêng
- `chat_panel.py`, `analytics_dialog.py`, `ai_search_dialog.py`, `settings_dialog.py`

**Hệ thống thiết kế**
- `theme_tokens.py` — bảng màu, spacing, radii, typography
- `theme_manager_v2.py` + `theme.qss` / `theme_dark.qss` — áp dụng theme sáng/tối
- `ui_icons.py` + `icons/lucide/` — nạp SVG, tô màu và cache thành `QIcon`
- `font_loader.py` + `assets/fonts/` — đăng ký Be Vietnam Pro trước khi có widget

**Xử lý dữ liệu**
- `ocr_pipeline.py`, `llama_ocr.py`, `chandra_field_parser.py` — OCR và bóc tách trường
- `document_repository.py` — SQLite, versioning, backup
- `vector_index.py`, `search_store.py`, `retrieval.py`, `reranker.py` — tìm kiếm
- `rag_agent.py`, `agent_tools.py`, `llm_client.py` — vòng lặp agent trả lời có dẫn nguồn
- `background_workers.py` — bảy worker nền của pipeline (OCR, batch, nhúng, quan hệ
  pháp lý, lập chỉ mục lại, agent, sửa tệp); `test_architecture_boundaries.py` cấm
  `main_window_1.py` tự định nghĩa lại chúng. Vẫn còn `QThread` phục vụ riêng từng
  widget ở `chat_panel`, `pdf_load_worker`, `startup_preloader`, `status_indicators`,
  `gpu_keepalive` và `ai_search_dialog`.

## Chạy ứng dụng

```bash
start.bat
```

## Chạy test

Runtime nhúng không kèm `pytest`. Cài vào một thư mục riêng để không làm bẩn
package portable:

```bash
runtime/python.exe -m pip install --target .devtools pytest
```

Vì bản Python nhúng có `python39._pth`, biến `PYTHONPATH` bị bỏ qua — nạp
thư mục trên bằng một script mồi:

```bash
runtime/python.exe -c "import sys,os; sys.path.insert(0,'.devtools'); os.environ['QT_QPA_PLATFORM']='offscreen'; import pytest; sys.exit(pytest.console_main())" tests/ -q
```

Một số test tự bỏ qua khi thiếu model GGUF hoặc corpus thật.

## CI trên Linux

Toàn bộ test chạy được trên Linux trong Docker, không cần model:

```bash
docker build -f docker/Dockerfile.ci -t ocr-vbhc-ci . && docker run --rm ocr-vbhc-ci
```

Image chỉ chứa mã nguồn (~5 MB context) và pin Python 3.9 + PyTorch 2.8 CPU đúng
theo `build_portable.PYTHON_VERSION` và `TORCH_VERSION`, để CI phân giải cùng bộ
wheel với bản portable. PyQtWebEngine bị lược bỏ có chủ đích — `landing_page.py`
đã guard import và `tests/test_landing.py` chạy nhánh dự phòng bằng
`force_native=True`.

**Đây không phải image chạy ứng dụng.** OCR VBHC là chương trình desktop PyQt5,
không có HTTP endpoint; image này chỉ để kiểm thử.

`.github/workflows/ci.yml` chạy bộ test trên mỗi push. Với mạng air-gap, kích hoạt
thủ công workflow kèm `export_airgap_bundle` để nhận tarball `docker save` + SHA256
mang vào bằng tay:

```bash
gunzip -c ocr-vbhc-ci.tar.gz | docker load
```

## Ràng buộc kiến trúc

`tests/test_architecture_boundaries.py` giữ cho `main_window_1.py` không phình
ngược trở lại thành god-object: nó fail nếu file này định nghĩa lại worker nền,
`DocumentOCR`, lớp repository, phương thức trùng tên, hoặc dùng theme manager cũ.

`tests/test_portable_manifest.py` bảo đảm mọi file mới đều được `build_portable.py`
đóng gói và `verify.bat` kiểm tra — thêm module mà quên khai báo sẽ fail test.

`tests/test_airgap.py` và `tools/check_airgap.py` chặn việc vô tình đưa vào
network call hay dependency cần internet.

## Quy ước đóng góp

- Thêm file nguồn mới ⇒ khai báo trong `CORE_PY_FILES` hoặc `STATIC_FILES` của
  `build_portable.py`, và bổ sung vào `verify.bat`.
- Thêm màu ⇒ đặt trong `theme_tokens.py`, không hardcode hex trong widget.
- Thêm lệnh/phím tắt ⇒ khai báo trong `ui_commands.COMMANDS`, không rải rác.
- Giữ Python tương thích 3.9 (không `match`, không `X | Y` ở runtime).
- Nhánh làm việc xếp chồng theo thứ tự phụ thuộc để test xanh ở từng bước.

## Giấy phép bên thứ ba

Xem [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) — GSAP, Be Vietnam Pro,
biểu tượng Lucide.
