"""Bounded in-memory RAR reading using an optional system libarchive.

No shell, filesystem extraction, or execution of archive contents.
"""
import ctypes
import ctypes.util


def read_rar(data, *, max_bytes=4_000_000, max_files=100):
    if not data.startswith(b"Rar!\x1a\x07"):
        raise ValueError("RAR 文件格式无效，请人工查看")
    name = ctypes.util.find_library("archive")
    if not name:
        raise ValueError("RAR 需要系统 libarchive，请转换为 ZIP 或人工查看")
    try:
        lib = ctypes.CDLL(name)
    except OSError:
        raise ValueError("系统 RAR 读取库无法加载，请转换为 ZIP 或人工查看") from None
    pointer = ctypes.c_void_p
    signatures = {
        "archive_read_new": (pointer, []),
        "archive_read_support_format_rar": (ctypes.c_int, [pointer]),
        "archive_read_open_memory": (ctypes.c_int, [pointer, pointer, ctypes.c_size_t]),
        "archive_read_next_header": (ctypes.c_int, [pointer, ctypes.POINTER(pointer)]),
        "archive_read_data": (ctypes.c_ssize_t, [pointer, pointer, ctypes.c_size_t]),
        "archive_read_free": (ctypes.c_int, [pointer]),
        "archive_entry_filetype": (ctypes.c_uint, [pointer]),
        "archive_entry_size": (ctypes.c_int64, [pointer]),
        "archive_entry_pathname_utf8": (ctypes.c_char_p, [pointer]),
    }
    for function, (result, args) in signatures.items():
        getattr(lib, function).restype = result
        getattr(lib, function).argtypes = args
    archive = lib.archive_read_new()
    if not archive:
        raise ValueError("RAR 读取器初始化失败")
    source = ctypes.create_string_buffer(data)
    buffer = ctypes.create_string_buffer(65536)
    files, total, count = [], 0, 0
    try:
        lib.archive_read_support_format_rar(archive)
        if hasattr(lib, "archive_read_support_format_rar5"):
            lib.archive_read_support_format_rar5.argtypes = [pointer]
            lib.archive_read_support_format_rar5.restype = ctypes.c_int
            lib.archive_read_support_format_rar5(archive)
        if lib.archive_read_open_memory(archive, source, len(data)) != 0:
            raise ValueError("RAR 无法打开，可能已损坏、加密或需要其他分卷")
        entry = pointer()
        while True:
            status = lib.archive_read_next_header(archive, ctypes.byref(entry))
            if status == 1:  # ARCHIVE_EOF
                break
            if status != 0:
                raise ValueError("RAR 内容无法完整读取，请人工查看")
            count += 1
            if count > max_files:
                raise ValueError("RAR 超过文件数限制")
            kind = lib.archive_entry_filetype(entry)
            if kind == 0o040000:
                continue
            if kind != 0o100000:
                raise ValueError("RAR 包含非普通文件，请人工查看")
            size = lib.archive_entry_size(entry)
            if size < 0 or total + size > max_bytes:
                raise ValueError("RAR 超过解压大小限制")
            filename = lib.archive_entry_pathname_utf8(entry)
            if not filename:
                raise ValueError("RAR 文件名无法识别，请人工查看")
            chunks, length = [], 0
            while True:
                amount = lib.archive_read_data(archive, buffer, len(buffer))
                if amount == 0:
                    break
                if amount < 0:
                    raise ValueError("RAR 文件内容无法读取，可能已损坏或加密")
                length += amount
                if total + length > max_bytes:
                    raise ValueError("RAR 超过解压大小限制")
                chunks.append(buffer.raw[:amount])
            if length != size:
                raise ValueError("RAR 文件大小不一致，请人工查看")
            total += length
            files.append((filename.decode("utf-8", errors="replace"), b"".join(chunks)))
    finally:
        lib.archive_read_free(archive)
    return files
