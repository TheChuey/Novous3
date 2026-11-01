"""File-manager doorway: REST (/api/file-manager/*) and elFinder (/api/elfinder/connector) routes."""

import base64
import mimetypes
from pathlib import PurePosixPath

from fastapi import APIRouter, HTTPException, Request

from file_manager.logic_file_manager import file_backend
from file_manager.schemas_file_manager import (
    CreateRequest,
    FileContentRequest,
    ManageRootsRequest,
    MoveRequest,
    PathRequest,
    SelectRootRequest,
)

router = APIRouter()

_VOLUME = "l1_"
_ELFINDER_API = "2.1"


@router.get("/api/file-manager/roots")
def api_file_manager_roots():
    return {
        "roots": file_backend.configured_roots(),
        "missing": file_backend.missing_roots(),
        "active_path": str(file_backend.workspace_root()),
    }


@router.post("/api/file-manager/roots/select")
def api_select_file_manager_root(request: SelectRootRequest):
    try:
        return file_backend.select_root(request.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/file-manager/roots/manage")
def api_manage_file_manager_roots(request: ManageRootsRequest):
    try:
        return file_backend.manage_roots(request.action, request.paths)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, FileExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, IsADirectoryError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, NotADirectoryError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, (ValueError, PermissionError)):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


@router.get("/api/file-manager/list")
def api_list_directory(path: str = ""):
    try:
        return file_backend.list_directory(path)
    except (FileNotFoundError, FileExistsError, IsADirectoryError,
            NotADirectoryError, ValueError, PermissionError) as exc:
        raise _http_error(exc) from exc


@router.get("/api/file-manager/tree")
def api_directory_tree(path: str = ""):
    try:
        return file_backend.directory_tree(path)
    except (FileNotFoundError, FileExistsError, IsADirectoryError,
            NotADirectoryError, ValueError, PermissionError) as exc:
        raise _http_error(exc) from exc


@router.get("/api/file-manager/read")
def api_read_file(path: str):
    try:
        return file_backend.read_file(path)
    except (FileNotFoundError, FileExistsError, IsADirectoryError,
            NotADirectoryError, ValueError, PermissionError) as exc:
        raise _http_error(exc) from exc


@router.post("/api/file-manager/write")
def api_write_file(request: FileContentRequest):
    try:
        return file_backend.write_file(request.path, request.content)
    except (FileNotFoundError, FileExistsError, IsADirectoryError,
            NotADirectoryError, ValueError, PermissionError) as exc:
        raise _http_error(exc) from exc


@router.post("/api/file-manager/create")
def api_create_path(request: CreateRequest):
    try:
        return file_backend.create_path(request.path, request.kind)
    except (FileNotFoundError, FileExistsError, IsADirectoryError,
            NotADirectoryError, ValueError, PermissionError) as exc:
        raise _http_error(exc) from exc


@router.post("/api/file-manager/delete")
def api_delete_path(request: PathRequest):
    try:
        return file_backend.delete_path(request.path)
    except (FileNotFoundError, FileExistsError, IsADirectoryError,
            NotADirectoryError, ValueError, PermissionError) as exc:
        raise _http_error(exc) from exc


@router.post("/api/file-manager/move")
def api_move_path(request: MoveRequest):
    try:
        return file_backend.move_path(
            request.source_path, request.destination_path
        )
    except (FileNotFoundError, FileExistsError, IsADirectoryError,
            NotADirectoryError, ValueError, PermissionError) as exc:
        raise _http_error(exc) from exc


def _encode_hash(path: str) -> str:
    normalized = path.strip("/") or "/"
    token = base64.urlsafe_b64encode(normalized.encode("utf-8")).decode("ascii")
    return _VOLUME + token.rstrip("=")


def _decode_hash(file_hash: str) -> str:
    if not isinstance(file_hash, str) or not file_hash.startswith(_VOLUME):
        raise ValueError("Invalid elFinder target.")
    token = file_hash[len(_VOLUME):]
    if not token or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in token):
        raise ValueError("Invalid elFinder target.")
    padded = token + "=" * (-len(token) % 4)
    try:
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid elFinder target.") from exc
    if _encode_hash(decoded) != file_hash:
        raise ValueError("Invalid elFinder target.")
    return "" if decoded == "/" else decoded


def _entry(path: str, entry: dict, parent: str) -> dict:
    item_hash = _encode_hash(path)
    is_directory = entry["type"] == "directory"
    return {
        "name": entry["name"],
        "hash": item_hash,
        "phash": _encode_hash(parent),
        "volumeid": _VOLUME,
        "mime": "directory" if is_directory else (
            mimetypes.guess_type(entry["name"])[0] or "application/octet-stream"
        ),
        "ts": int(entry["modified"]),
        "size": entry["size"],
        "read": 1,
        "write": 1,
        "locked": 0,
        "dirs": 1 if is_directory else 0,
        "path": path,
    }


def _directory_contents(path: str) -> list[dict]:
    listing = file_backend.list_directory(path)
    return [
        _entry(item["path"], item, path)
        for item in listing["entries"]
    ]


def _directory_tree(path: str) -> list[dict]:
    entries = _directory_contents(path)
    directories = []
    for entry in entries:
        if entry["mime"] == "directory":
            directories.append({
                "name": entry["name"],
                "hash": entry["hash"],
                "phash": entry["phash"],
                "path": entry["path"],
            })
            directories.extend(_directory_tree(entry["path"]))
    return directories


def _file_info(path: str) -> dict:
    resolved = file_backend.resolve_workspace_path(path)
    parent = PurePosixPath(path).parent.as_posix()
    if parent == ".":
        parent = ""
    if resolved.is_dir():
        entries = file_backend.list_directory(path)["entries"]
        entry = {
            "name": resolved.name if path else "workspace",
            "path": path,
            "type": "directory",
            "size": 0,
            "modified": resolved.stat().st_mtime,
        }
        info = _entry(path, entry, parent)
        info["dirs"] = sum(item["type"] == "directory" for item in entries)
        return info
    stat = resolved.stat()
    return _entry(path, {
        "name": resolved.name,
        "path": resolved.relative_to(file_backend.workspace_root()).as_posix(),
        "type": "file",
        "size": stat.st_size,
        "modified": stat.st_mtime,
    }, parent)


def _child_path(parent: str, name: str) -> str:
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("A valid file or directory name is required.")
    return f"{parent}/{name}" if parent else name


def _values(data, key: str) -> list[str]:
    if hasattr(data, "getlist"):
        values = data.getlist(f"{key}[]") or data.getlist(key)
    else:
        values = data.get(f"{key}[]", data.get(key, []))
    if isinstance(values, str):
        return [values]
    return list(values or [])


async def _connector_data(request: Request):
    if "application/json" in request.headers.get("content-type", ""):
        return await request.json()
    return await request.form()


@router.post("/api/elfinder/connector")
async def elfinder_connector(request: Request):
    data = await _connector_data(request)
    command = data.get("cmd", "")
    try:
        if command == "open":
            path = _decode_hash(data.get("target", _encode_hash("/")))
            cwd = _file_info(path)
            if cwd["mime"] != "directory":
                raise ValueError("The requested target is not a directory.")
            return {
                "cwd": cwd,
                "files": [cwd, *_directory_contents(path)],
                "api": _ELFINDER_API,
                "options": {
                    "path": path,
                    "url": "/api/elfinder/connector",
                    "disabled": [],
                    "separator": "/",
                    "archivers": {"create": [], "extract": []},
                },
            }

        if command == "tree":
            path = _decode_hash(data.get("target", _encode_hash("/")))
            return {"tree": _directory_tree(path)}

        if command == "ls":
            path = _decode_hash(data.get("target", ""))
            return {
                "list": {
                    item["hash"]: item["name"]
                    for item in _directory_contents(path)
                }
            }

        if command in {"mkfile", "mkdir"}:
            parent = _decode_hash(data.get("target", ""))
            name = data.get("name", "")
            path = _child_path(parent, name)
            result = file_backend.create_path(
                path, "directory" if command == "mkdir" else "file"
            )
            return {"added": [_file_info(result["path"])]}

        if command == "rename":
            source = _decode_hash(data.get("target", ""))
            resolved_source = file_backend.resolve_workspace_path(source)
            if not source or not resolved_source.exists():
                raise FileNotFoundError(f"Path not found: {source}")
            parent = PurePosixPath(source).parent.as_posix()
            destination = _child_path("" if parent == "." else parent, data.get("name", ""))
            result = file_backend.move_path(source, destination)
            return {"added": [_file_info(result["destination_path"])],
                    "removed": [_encode_hash(source)]}

        if command == "paste":
            destination = _decode_hash(data.get("dst", ""))
            cut = str(data.get("cut", "0")) in {"1", "true", "True"}
            added = []
            removed = []
            for source_hash in _values(data, "src"):
                source = _decode_hash(source_hash)
                source_path = file_backend.resolve_workspace_path(source)
                target_path = _child_path(destination, source_path.name)
                result = (
                    file_backend.move_path(source, target_path)
                    if cut else file_backend.copy_path(source, target_path)
                )
                added.append(_file_info(result["destination_path"]))
                if cut:
                    removed.append(source_hash)
            return {"added": added, "removed": removed}

        if command == "rm":
            targets = _values(data, "targets")
            removed = []
            for target in targets:
                path = _decode_hash(target)
                file_backend.delete_path(path)
                removed.append(target)
            return {"removed": removed}

        if command == "put":
            path = _decode_hash(data.get("target", ""))
            result = file_backend.write_file(path, data.get("content", ""))
            return {"changed": [_file_info(result["path"])]}

        raise ValueError(f"Unsupported elFinder command: {command or '(missing)'}")
    except (FileNotFoundError, FileExistsError, IsADirectoryError,
            NotADirectoryError, ValueError, PermissionError) as exc:
        return {"error": [str(exc)]}
