# OCR VBHC Portable — Cách chạy trên máy khác

## Yêu cầu tối thiểu
- Windows 10/11 x64 (không cần cài Python, CUDA, hay gì khác)
- 20 GB trống ổ cứng
- 8 GB RAM (CPU) hoặc GPU NVIDIA 4GB+ VRAM (nhanh hơn 5-10x)

## Cách dùng

### Bước 1: Copy folder
Copy toàn bộ folder `OCR_VBHC_Portable/` vào máy đích (USB, ổ mạng, hoặc local disk).
**KHÔNG copy riêng lẻ file** — các đường dẫn relative yêu cầu cấu trúc folder đầy đủ.

### Bước 2: Chạy
Double-click `start.bat`.

App sẽ tự động:
1. Tìm `runtime/python.exe` (Python 3.9 embedded)
2. Thêm `tools/poppler/` vào PATH (PDF renderer)
3. Chạy `_detect_gpu.py` — phát hiện GPU
4. Chọn `bin/cuda/llama-server.exe` (nếu có GPU) hoặc `bin/cpu/llama-server.exe` (CPU fallback)
5. Khởi động `main.py`

### Bước 3: Chờ lần đầu
Lần chạy đầu mất **30-60 giây** để:
- Load Chandra OCR 2 Q4 model (~3GB vào RAM/VRAM)
- Khởi tạo DB SQLite (`database/documents.db` tự tạo)
- Start llama-server OCR background process (port 9999)

## Trợ lý AI pháp lý

Dock **Trợ lý AI** chạy hoàn toàn offline bằng llama-server thứ hai (port 9998).
Ứng dụng tự chọn Qwen3-8B khi VRAM đủ, nếu không sẽ dùng Qwen3-4B; CPU vẫn
chạy được nhưng chậm hơn. Câu trả lời có citation `[n]`; bấm citation để mở
đúng tài liệu/trang nguồn. Nội dung trả lời chỉ có tính tham khảo, không thay
thế tư vấn pháp lý chuyên nghiệp.

Trước khi hỏi, dùng **Thêm ▾ → Thêm thư mục văn bản...** để OCR và index một
corpus. Batch dài lưu từng tài liệu ngay khi xong và bỏ qua đường dẫn đã nhập,
nên có thể tiếp tục sau khi dừng. Dock chat hiển thị số văn bản/chunk hiện có,
chỉ gửi tối đa 12 tin nhắn gần nhất làm ngữ cảnh, hỗ trợ tạo mới và xóa từng
phiên; reasoning nội bộ không được hiển thị hay lưu vào lịch sử.

## Cấu trúc folder

```
OCR_VBHC_Portable/
├── start.bat                  ← ENTRY POINT (double-click)
├── _detect_gpu.py             ← GPU detection (chạy bởi start.bat)
├── main.py, intro_page.py     ← Entry + splash screen
├── main_window_1.py           ← Main app (~7500 lines)
├── runtime/                   ← Python 3.9 embedded + deps (~5GB)
│   ├── python.exe
│   └── Lib/site-packages/     ← PyQt5, torch 2.8 CPU, transformers...
├── bin/                       ← llama.cpp binaries
│   ├── cpu/llama-server.exe   ← CPU-only backend (ggml-cpu-*.dll)
│   └── cuda/llama-server.exe  ← GPU backend (cublas, cudart, cudnn)
├── gguf/                      ← OCR + chat model files (~10.8GB)
│   ├── chandra/
│   │   ├── chandra-ocr-2-q4_k_m.gguf   ← Main model 2.9GB
│   │   └── chandra-ocr-2.mmproj-q8_0.gguf  ← Vision projector 349MB
│   └── chat/
│       ├── Qwen3-4B-Instruct-2507-Q4_K_M.gguf  ← Chat nhẹ (~2.5GB)
│       └── Qwen3-8B-Q4_K_M.gguf                ← Chat chất lượng cao (~5.0GB)
├── model_embedding/           ← BGE-M3 Vietnamese embedding (~2.2GB)
├── model_reranker/            ← ViRanker cross-encoder (~1.5GB)
├── tools/poppler/             ← PDF renderer
├── icons/                     ← Lucide SVG icons + app icons
├── config/                    ← User settings (tự tạo khi chạy)
├── database/                  ← SQLite DB (tự tạo khi chạy)
├── logs/                      ← Runtime logs
├── temp/                      ← Temp images (tự xóa)
└── output/                    ← Kết quả Excel/Word xuất
```

## Troubleshooting

### "Khong tim thay runtime\python.exe"
→ Folder bị copy thiếu. Copy lại toàn bộ từ nguồn gốc.

### App close với exit code ≠ 0
→ Mở `logs/ocr_app.log` để xem chi tiết.

### OCR chậm
→ Kiểm tra log "Backend: CPU" vs "Backend: GPU (CUDA)".
- CPU thường: ~90-120s/trang
- GPU RTX 3060+: ~15-25s/trang

### "Port 9999 already in use"
→ Có llama-server zombie từ phiên trước. App tự động kill, hoặc:
```cmd
taskkill /IM llama-server.exe /F
```

## Test trên máy sạch (optional — Windows Sandbox)

Nếu máy có Windows 11 Pro/Enterprise với Sandbox enabled:
1. Double-click `OCR_Portable_Sandbox_Test.wsb`
2. Windows Sandbox khởi động (~5s) với clean Windows 11 VM
3. Auto-copy package + chạy `start.bat`

**Cách enable Windows Sandbox**:
- Control Panel → Programs → Turn Windows features on or off
- Check "Windows Sandbox" → OK → Restart

## Rebuild package

Nếu cập nhật code, rebuild package:
```cmd
cd desktop
python build_portable.py                   # CPU-only (~10GB)
python build_portable.py --include-gpu     # + GPU wheels (~12GB)
```

Kết quả: `OCR_VBHC_Portable/` folder mới sẵn sàng copy USB.

## Changelog

Xem `VERSION.txt`.
