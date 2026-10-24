"""One declarative command catalogue for toolbar, palette and shortcut help."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    id: str
    label: str
    group: str
    shortcut: str = ""
    icon: str = ""
    landing_label: str = ""


COMMANDS = (
    CommandSpec("home", "Bàn bắt đầu việc", "Điều hướng", "Alt+H", "app-mark-solid"),
    CommandSpec("add_file", "Thêm một văn bản", "Tệp", "Ctrl+O", "file-plus", "Quét một văn bản"),
    CommandSpec("add_files", "Thêm nhiều tệp", "Tệp", "Ctrl+Shift+O", "files"),
    CommandSpec("add_folder", "Nhập thư mục văn bản", "Tệp", "Ctrl+Alt+O", "folder-open"),
    CommandSpec("save", "Lưu thay đổi", "Chỉnh sửa", "Ctrl+S", "save"),
    CommandSpec("reset", "Đặt lại biểu mẫu", "Chỉnh sửa", "Ctrl+Shift+Backspace", "undo-2"),
    CommandSpec("delete", "Xóa văn bản", "Chỉnh sửa", "Del", "trash-2"),
    CommandSpec("search", "Tìm kiếm văn bản", "Tìm kiếm", "Ctrl+F", "search"),
    CommandSpec("ai_search", "Tìm kiếm AI ngữ nghĩa", "Tìm kiếm", "Ctrl+Shift+F", "sparkles"),
    CommandSpec("chat", "Mở trợ lý pháp luật", "Tìm kiếm", "Ctrl+Shift+K", "chat", "Hỏi trợ lý pháp luật"),
    CommandSpec("analytics", "Phân tích thống kê", "Báo cáo", "Ctrl+Shift+A", "bar-chart-3"),
    CommandSpec("export_excel", "Xuất dữ liệu Excel", "Báo cáo", "Ctrl+E", "excel"),
    CommandSpec("toggle_theme", "Đổi chế độ sáng/tối", "Hiển thị", "Ctrl+D", "moon"),
    CommandSpec("toggle_library", "Ẩn/hiện kho văn bản", "Hiển thị", "F9", "panel-left", "Mở kho văn bản"),
    CommandSpec("toggle_metadata", "Ẩn/hiện vùng chi tiết", "Hiển thị", "F10", "panel-right"),
    CommandSpec("preview", "Xem trước PDF", "Hiển thị", "Ctrl+P", "eye"),
    CommandSpec("refresh", "Làm mới dữ liệu", "Hiển thị", "F5", "refresh-cw"),
    CommandSpec("settings", "Mở cài đặt", "Hiển thị", "Ctrl+,", "settings"),
    CommandSpec("previous_page", "Trang trước", "PDF", "Alt+Left"),
    CommandSpec("next_page", "Trang sau", "PDF", "Alt+Right"),
    CommandSpec("zoom_in", "Phóng to PDF", "PDF", "Ctrl++"),
    CommandSpec("zoom_out", "Thu nhỏ PDF", "PDF", "Ctrl+-"),
    CommandSpec("zoom_reset", "Đặt lại thu phóng", "PDF", "Ctrl+0"),
    CommandSpec("rotate_right", "Xoay phải", "PDF", "Ctrl+R"),
    CommandSpec("rotate_left", "Xoay trái", "PDF", "Ctrl+Shift+R"),
    CommandSpec("command_palette", "Tìm và chạy tác vụ", "Trợ giúp", "Ctrl+K", "command"),
    CommandSpec("shortcut_help", "Hiện danh sách phím tắt", "Trợ giúp", "F1", "help-circle"),
)
_BY_ID = {spec.id: spec for spec in COMMANDS}


def command(command_id):
    return _BY_ID[command_id]


def shortcut_rows():
    return [
        (spec.shortcut, spec.label, spec.group)
        for spec in COMMANDS if spec.shortcut
    ]


def landing_action_specs():
    """Serialize the landing subset without duplicating labels or shortcuts."""
    action_to_command = {
        "open_file": "add_file",
        "open_files": "add_files",
        "import_folder": "add_folder",
        "open_library": "toggle_library",
        "open_chat": "chat",
    }
    return [
        {
            "action": action,
            "commandId": spec.id,
            "label": spec.landing_label or spec.label,
            "shortcut": spec.shortcut,
            "icon": spec.icon,
        }
        for action, command_id in action_to_command.items()
        for spec in (command(command_id),)
    ]
