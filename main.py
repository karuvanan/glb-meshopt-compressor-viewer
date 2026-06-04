# -*- coding: utf-8 -*-
"""
GLB Meshopt Batch Compressor + Three.js Viewer - PyQt6 Integrated Version

功能：
1. 左侧批次选择多个 .glb 文件。
2. 支持 Meshopt 低损压缩。
3. 支持 Simplify 减面后再 Meshopt 二次压缩。
4. 压缩完成后，点击列表中的文件即可在右侧 Three.js 预览。
5. Three.js 预览器已启用 MeshoptDecoder，可打开 EXT_meshopt_compression 的 GLB。

依赖：
    pip install PyQt6 PyQt6-WebEngine

外部命令：
    npm install -g @gltf-transform/cli

Windows 一般会调用：gltf-transform.cmd
macOS / Linux 一般会调用：gltf-transform
"""

import os
import sys
import shutil
import subprocess
import threading
import mimetypes
import urllib.request
import ssl
import json
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote, quote
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QAction, QColor
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QRadioButton,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except Exception as exc:  # pragma: no cover
    QWebEngineView = None
    QWebEngineSettings = None
    WEBENGINE_IMPORT_ERROR = exc
    QWebEnginePage = None
else:
    WEBENGINE_IMPORT_ERROR = None


@dataclass
class FileJob:
    source_path: str
    output_path: str
    status: str = "等待处理"
    original_mb: float = 0.0
    compressed_mb: float = 0.0
    reduction_pct: float = 0.0
    error: str = ""



class FullJsmDownloadWorker(QThread):
    log_message = pyqtSignal(str)
    progress_changed = pyqtSignal(int, int)
    finished_download = pyqtSignal(int, int, str)

    THREE_VERSION = "0.184.0"
    MESHOPT_VERSION = "0.20.0"

    def __init__(self, app_dir, parent=None):
        super().__init__(parent)
        self.app_dir = Path(app_dir)
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def _urlopen_bytes(self, url, timeout=90):
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read()

    def _download_one(self, url, target):
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = self._urlopen_bytes(url)
        if len(data) <= 0:
            raise RuntimeError("下载内容为空")
        target.write_bytes(data)
        return len(data)

    def run(self):
        ok = 0
        failed = 0
        last_error = ""
        try:
            base = f"https://unpkg.com/three@{self.THREE_VERSION}"
            libs_root = self.app_dir / "libs" / "threejs"
            jsm_root = libs_root / "examples" / "jsm"

            self.log_message.emit(f"\n[开始下载 Three.js {self.THREE_VERSION} examples/jsm 全部文件]")
            self.log_message.emit("来源：" + f"https://app.unpkg.com/three@{self.THREE_VERSION}/files/examples/jsm")

            # 1) Three.js r184+ 的 build/three.module.js 已经不是单一文件，
            #    它会再 import ./three.core.js 等 build 依赖。
            #    之前只下载 three.module.js 会导致：
            #    Failed to fetch dynamically imported module: three.module.js
            #    所以这里改成下载 build/ 目录全部 JS 文件。
            build_meta_url = f"{base}/build/?meta"
            self.log_message.emit("读取 build 目录索引：" + build_meta_url)
            build_meta_data = self._urlopen_bytes(build_meta_url)
            build_meta = json.loads(build_meta_data.decode("utf-8"))
            build_files = []
            for item in build_meta.get("files", []):
                path = item.get("path", "")
                if path and not path.endswith("/") and path.startswith("/build/"):
                    build_files.append(path)
            if not build_files:
                raise RuntimeError("UNPKG build metadata 没有返回 files 清单")

            self.log_message.emit(f"build 文件数量：{len(build_files)}")
            for remote_path in build_files:
                rel = remote_path.removeprefix("/build/")
                encoded_path = "/".join(quote(part) for part in remote_path.split("/") if part)
                url = f"{base}/{encoded_path}"
                target = libs_root / rel
                try:
                    size = self._download_one(url, target)
                    ok += 1
                    if rel in ("three.module.js", "three.core.js", "three.webgpu.js", "three.tsl.js") or ok % 10 == 0:
                        self.log_message.emit(f"✅ build/{rel} ({size:,} bytes)")
                except Exception as exc:
                    failed += 1
                    last_error = f"build/{rel}: {exc}"
                    self.log_message.emit(f"❌ build/{rel}: {exc}")

            # 2) Use UNPKG metadata API to list every file under examples/jsm recursively.
            meta_url = f"{base}/examples/jsm/?meta"
            self.log_message.emit("读取目录索引：" + meta_url)
            meta_data = self._urlopen_bytes(meta_url)
            meta = json.loads(meta_data.decode("utf-8"))
            files = meta.get("files", [])
            if not files:
                raise RuntimeError("UNPKG metadata 没有返回 files 清单")

            # metadata paths start with /examples/jsm/...
            file_paths = []
            for item in files:
                path = item.get("path", "")
                if not path or path.endswith("/"):
                    continue
                if path.startswith("/examples/jsm/"):
                    file_paths.append(path)

            total = len(file_paths) + 2  # + core + fallback meshopt check/download
            self.progress_changed.emit(1, total)
            self.log_message.emit(f"目录内文件数量：{len(file_paths)}")

            for i, remote_path in enumerate(file_paths, start=2):
                if self._stop_requested:
                    self.log_message.emit("[已停止] 用户中止下载。")
                    break
                rel = remote_path.removeprefix("/examples/jsm/")
                # Quote only the path pieces to support files with special characters.
                encoded_path = "/".join(quote(part) for part in remote_path.split("/") if part)
                url = f"{base}/{encoded_path}"
                target = jsm_root / rel
                try:
                    size = self._download_one(url, target)
                    ok += 1
                    if ok % 25 == 0 or rel in ("controls/OrbitControls.js", "loaders/GLTFLoader.js", "libs/meshopt_decoder.module.js"):
                        self.log_message.emit(f"✅ {rel} ({size:,} bytes)")
                except Exception as exc:
                    failed += 1
                    last_error = f"{rel}: {exc}"
                    self.log_message.emit(f"❌ {rel}: {exc}")
                self.progress_changed.emit(i, total)

            # 3) Guarantee MeshoptDecoder exists at both expected paths.
            meshopt_in_three = jsm_root / "libs" / "meshopt_decoder.module.js"
            meshopt_alt = self.app_dir / "libs" / "meshoptimizer" / "meshopt_decoder.module.js"
            if meshopt_in_three.exists() and meshopt_in_three.stat().st_size > 0:
                meshopt_alt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(meshopt_in_three, meshopt_alt)
                self.log_message.emit(f"✅ MeshoptDecoder 兼容复制：{meshopt_alt}")
            else:
                fallback = f"https://unpkg.com/meshoptimizer@{self.MESHOPT_VERSION}/meshopt_decoder.module.js"
                self.log_message.emit("补下载 MeshoptDecoder：" + fallback)
                self._download_one(fallback, meshopt_in_three)
                meshopt_alt.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(meshopt_in_three, meshopt_alt)
                ok += 1

            self.progress_changed.emit(total, total)
            self.log_message.emit(f"[下载完成] 成功 {ok}，失败 {failed}")
        except Exception as exc:
            failed += 1
            last_error = str(exc)
            self.log_message.emit("❌ 下载流程失败：" + last_error)
        self.finished_download.emit(ok, failed, last_error)

class CompressorWorker(QThread):
    progress_changed = pyqtSignal(int, int)
    row_updated = pyqtSignal(int, object)
    log_message = pyqtSignal(str)
    finished_all = pyqtSignal()

    def __init__(self, jobs, mode, ratio, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.mode = mode
        self.ratio = ratio
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def _find_gltf_transform(self):
        candidates = [
            "gltf-transform.cmd",
            "gltf-transform.exe",
            "gltf-transform",
        ]
        for name in candidates:
            found = shutil.which(name)
            if found:
                return found
        return None

    def run(self):
        exe = self._find_gltf_transform()
        if not exe:
            self.log_message.emit(
                "❌ 找不到 gltf-transform。请先执行：npm install -g @gltf-transform/cli"
            )
            self.finished_all.emit()
            return

        total = len(self.jobs)
        self.log_message.emit(f"\n[开始批次处理] 共 {total} 个 GLB 文件")
        self.log_message.emit(f"使用命令：{exe}")

        for index, job in enumerate(self.jobs):
            if self._stop_requested:
                self.log_message.emit("\n[已停止] 用户中止批次处理。")
                break

            src = Path(job.source_path)
            out = Path(job.output_path)
            temp_simplified = out.with_name(out.stem + "__temp_simplified.glb")

            job.status = "处理中"
            self.row_updated.emit(index, job)
            self.progress_changed.emit(index, total)

            self.log_message.emit(f"\n正在处理 ({index + 1}/{total}): {src.name}")

            try:
                if out.exists():
                    out.unlink()
                if temp_simplified.exists():
                    temp_simplified.unlink()

                if self.mode == "simplify":
                    simplify_cmd = [
                        exe,
                        "simplify",
                        str(src),
                        str(temp_simplified),
                        "--ratio",
                        str(self.ratio),
                        "--error",
                        "0.001",
                    ]
                    self._run_command(simplify_cmd)

                    if not temp_simplified.exists() or temp_simplified.stat().st_size <= 0:
                        raise RuntimeError("Simplify 完成后没有生成有效临时文件。")

                    meshopt_cmd = [
                        exe,
                        "meshopt",
                        str(temp_simplified),
                        str(out),
                        "--level",
                        "high",
                    ]
                    self._run_command(meshopt_cmd)

                else:
                    meshopt_cmd = [
                        exe,
                        "meshopt",
                        str(src),
                        str(out),
                        "--level",
                        "high",
                    ]
                    self._run_command(meshopt_cmd)

                if temp_simplified.exists():
                    temp_simplified.unlink()

                if not out.exists() or out.stat().st_size <= 0:
                    raise RuntimeError("输出文件没有生成，或文件大小为 0。")

                original_size = src.stat().st_size
                compressed_size = out.stat().st_size
                job.original_mb = original_size / 1024 / 1024
                job.compressed_mb = compressed_size / 1024 / 1024
                job.reduction_pct = (1 - compressed_size / original_size) * 100 if original_size else 0
                job.status = "完成"
                job.error = ""

                self.log_message.emit("✅ 成功完成")
                self.log_message.emit(f"   原始大小：{job.original_mb:.2f} MB")
                self.log_message.emit(f"   压缩后：{job.compressed_mb:.2f} MB")
                self.log_message.emit(f"   缩小：{job.reduction_pct:.1f}%")
                self.log_message.emit(f"   输出：{out}")

            except Exception as exc:
                job.status = "失败"
                job.error = str(exc)
                self.log_message.emit(f"❌ 失败：{exc}")
                try:
                    if temp_simplified.exists():
                        temp_simplified.unlink()
                except Exception:
                    pass

            self.row_updated.emit(index, job)
            self.progress_changed.emit(index + 1, total)

        self.log_message.emit("\n[处理结束]")
        self.finished_all.emit()

    def _run_command(self, cmd):
        self.log_message.emit("$ " + " ".join(f'\"{x}\"' if " " in x else x for x in cmd))
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            shell=False,
        )
        if result.stdout.strip():
            self.log_message.emit(result.stdout.strip())
        if result.stderr.strip():
            self.log_message.emit(result.stderr.strip())
        if result.returncode != 0:
            raise RuntimeError(f"gltf-transform 返回错误码 {result.returncode}")


class LocalAssetRequestHandler(BaseHTTPRequestHandler):
    """Serve local Three.js libraries and selected GLB files through localhost HTTP.

    This avoids PyQt WebEngine file:// module/importmap problems while keeping the
    original Online viewer architecture: HTML + importmap + window.loadModel().
    """
    app_dir = Path(__file__).resolve().parent

    def log_message(self, fmt, *args):
        # Keep CMD clean. Comment this out if low-level HTTP debugging is needed.
        return

    def _send_bytes(self, data: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, content_type: str | None = None):
        try:
            path = path.resolve()
            if not path.exists() or not path.is_file():
                self.send_error(404, f"File not found: {path}")
                return
            if content_type is None:
                content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            if path.suffix.lower() in {".js", ".mjs"}:
                content_type = "application/javascript; charset=utf-8"
            elif path.suffix.lower() == ".glb":
                content_type = "model/gltf-binary"
            data = path.read_bytes()
            self._send_bytes(data, content_type)
        except Exception as exc:
            self.send_error(500, str(exc))

    def do_GET(self):
        parsed = urlparse(self.path)
        request_path = unquote(parsed.path)

        if request_path == "/ping":
            self._send_bytes(b"OK", "text/plain; charset=utf-8")
            return

        if request_path == "/model":
            qs = parse_qs(parsed.query)
            model_path = qs.get("path", [""])[0]
            if not model_path:
                self.send_error(400, "Missing model path")
                return
            self._send_file(Path(model_path), "model/gltf-binary")
            return

        # Only expose libs folder from this app directory.
        if request_path.startswith("/libs/"):
            rel = request_path.lstrip("/").replace("/", os.sep)
            target = (self.app_dir / rel).resolve()
            libs_root = (self.app_dir / "libs").resolve()
            try:
                target.relative_to(libs_root)
            except ValueError:
                self.send_error(403, "Forbidden")
                return
            self._send_file(target)
            return

        self.send_error(404, "Not found")



class DebugWebEnginePage(QWebEnginePage):
    console_message = pyqtSignal(str)

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        try:
            level_name = getattr(level, "name", str(level))
        except Exception:
            level_name = str(level)
        text = f"[Viewer JS Console] {level_name} line {lineNumber}: {message} | source={sourceID}"
        print(text)
        self.console_message.emit(text)
        super().javaScriptConsoleMessage(level, message, lineNumber, sourceID)

class ThreeJsViewer(QWidget):
    viewer_log = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.app_dir = Path(__file__).resolve().parent
        self.httpd = None
        self.http_thread = None
        self.base_url = None
        self.pending_model_url = None
        self.page_ready = False

        if QWebEngineView is None:
            msg = QLabel(
                "PyQt6 WebEngine 未安装。\n\n"
                "请执行：\n"
                "pip install PyQt6-WebEngine\n\n"
                f"导入错误：{WEBENGINE_IMPORT_ERROR}"
            )
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            msg.setStyleSheet("font-size: 15px; color: #b00020; padding: 30px;")
            layout.addWidget(msg)
            self.web = None
            return

        self._start_local_server()

        self.web = QWebEngineView(self)
        if QWebEnginePage is not None:
            self.debug_page = DebugWebEnginePage(self.web)
            self.debug_page.console_message.connect(self.viewer_log.emit)
            self.web.setPage(self.debug_page)
        settings = self.web.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        layout.addWidget(self.web)

        self.web.loadFinished.connect(self._on_load_finished)
        # Important: baseUrl is localhost HTTP, not file://. This makes ES modules,
        # importmap, GLTFLoader and MeshoptDecoder load reliably in PyQt WebEngine.
        self.web.setHtml(self._viewer_html(), QUrl(self.base_url + "/"))

    def _start_local_server(self):
        LocalAssetRequestHandler.app_dir = self.app_dir
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), LocalAssetRequestHandler)
        port = self.httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{port}"
        self.http_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.http_thread.start()
        print(f"[Viewer HTTP] {self.base_url}")
        try:
            self.viewer_log.emit(f"[Viewer HTTP] {self.base_url}")
        except Exception:
            pass

    def _on_load_finished(self, ok: bool):
        self.page_ready = bool(ok)
        self.viewer_log.emit(f"[Viewer] loadFinished ok={ok}")
        if self.pending_model_url:
            url = self.pending_model_url
            self.pending_model_url = None
            self._run_load_model(url)

    def _model_http_url(self, file_path: str) -> str:
        # Use localhost HTTP for GLB too. This avoids file:// restrictions, OneDrive
        # paths, spaces, Chinese paths and CORS issues.
        from urllib.parse import quote
        absolute = os.path.abspath(file_path)
        return f"{self.base_url}/model?path={quote(absolute)}"

    def load_glb(self, file_path):
        if not self.web:
            return
        model_url = self._model_http_url(file_path)
        self.viewer_log.emit(f"[Viewer] load_glb file={file_path}")
        self.viewer_log.emit(f"[Viewer] model_url={model_url}")
        if not self.page_ready:
            self.pending_model_url = model_url
            self.viewer_log.emit("[Viewer] page not ready, model queued")
            return
        self._run_load_model(model_url)

    def _run_load_model(self, model_url: str):
        # Keep same architecture as the working Online version: call window.loadModel.
        # The HTML also defines an early stub, so even very early calls are queued.
        js = f"window.loadModel({model_url!r});"
        self.viewer_log.emit("[Viewer] runJavaScript window.loadModel(...)")
        self.web.page().runJavaScript(js, lambda result: self.viewer_log.emit(f"[Viewer] runJavaScript result={result!r}"))

    def diagnostic_report(self):
        lines = []
        lines.append("\n[Viewer 诊断报告]")
        lines.append(f"app_dir={self.app_dir}")
        lines.append(f"base_url={self.base_url}")
        paths = {
            "three.module.js": self.app_dir / "libs" / "threejs" / "three.module.js",
            "OrbitControls.js": self.app_dir / "libs" / "threejs" / "examples" / "jsm" / "controls" / "OrbitControls.js",
            "GLTFLoader.js": self.app_dir / "libs" / "threejs" / "examples" / "jsm" / "loaders" / "GLTFLoader.js",
            "meshopt_decoder.module.js": self.app_dir / "libs" / "threejs" / "examples" / "jsm" / "libs" / "meshopt_decoder.module.js",
        }
        for name, path in paths.items():
            if path.exists():
                lines.append(f"✅ {name}: {path} ({path.stat().st_size:,} bytes)")
            else:
                lines.append(f"❌ {name}: {path}")
        test_urls = [
            self.base_url + "/ping",
            self.base_url + "/libs/threejs/three.module.js",
            self.base_url + "/libs/threejs/examples/jsm/controls/OrbitControls.js",
            self.base_url + "/libs/threejs/examples/jsm/loaders/GLTFLoader.js",
            self.base_url + "/libs/threejs/examples/jsm/libs/meshopt_decoder.module.js",
        ]
        for url in test_urls:
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    data = resp.read(120)
                    lines.append(f"HTTP {resp.status} OK: {url} | first={data[:60]!r}")
            except Exception as exc:
                lines.append(f"HTTP FAIL: {url} | {exc}")
        return "\n".join(lines)

    def _viewer_html(self):
        html = r"""
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>GLB Three.js Meshopt Viewer</title>
<style>
    html, body {
        margin: 0;
        padding: 0;
        width: 100%;
        height: 100%;
        overflow: hidden;
        background: #111827;
        font-family: Arial, "Microsoft JhengHei", sans-serif;
        color: #e5e7eb;
    }
    #viewer {
        position: fixed;
        inset: 0;
    }
    #topbar {
        position: fixed;
        left: 12px;
        right: 12px;
        top: 12px;
        z-index: 10;
        background: rgba(17, 24, 39, 0.82);
        border: 1px solid rgba(255,255,255,0.12);
        border-radius: 10px;
        padding: 10px 12px;
        backdrop-filter: blur(8px);
        box-shadow: 0 10px 28px rgba(0,0,0,0.35);
    }
    #title {
        font-size: 14px;
        font-weight: 700;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    #status {
        margin-top: 4px;
        font-size: 12px;
        color: #9ca3af;
    }
    #debuglog {
        position: fixed;
        left: 12px;
        right: 12px;
        bottom: 12px;
        max-height: 180px;
        overflow: auto;
        z-index: 20;
        background: rgba(0,0,0,0.70);
        border: 1px solid rgba(255,255,255,0.15);
        border-radius: 8px;
        padding: 8px 10px;
        font-family: Consolas, monospace;
        font-size: 11px;
        color: #d1d5db;
        white-space: pre-wrap;
    }
    #hint {
        position: fixed;
        left: 50%;
        top: 50%;
        transform: translate(-50%, -50%);
        text-align: center;
        color: #9ca3af;
        font-size: 16px;
        line-height: 1.8;
        pointer-events: none;
    }
</style>
<script>
window.__pendingModelUrl = null;
window.__viewerReady = false;
window.__debugLines = [];
window.__vlog = function(msg) {
    const line = new Date().toLocaleTimeString() + ' | ' + msg;
    window.__debugLines.push(line);
    if (window.__debugLines.length > 80) window.__debugLines.shift();
    const box = document.getElementById('debuglog');
    if (box) box.textContent = window.__debugLines.join('\n');
    console.log('[VIEWER]', msg);
};
window.__vlog('HTML bootstrap script executed');
window.loadModel = function(url) {
    window.__pendingModelUrl = url;
    window.__vlog('loadModel stub queued: ' + url);
    const s = document.getElementById('status');
    if (s) s.textContent = 'Viewer 初始化中，模型已排队...';
};
window.addEventListener('error', (e) => {
    const msg = e.message || e.error || 'Unknown error';
    const s = document.getElementById('status');
    if (s) s.textContent = '❌ Viewer JS 错误：' + msg;
    window.__vlog('window.error: ' + msg + ' line=' + e.lineno + ' source=' + e.filename);
    console.error(msg);
});
window.addEventListener('unhandledrejection', (e) => {
    const msg = e.reason && e.reason.message ? e.reason.message : String(e.reason);
    const s = document.getElementById('status');
    if (s) s.textContent = '❌ Viewer 模组载入失败：' + msg;
    window.__vlog('unhandledrejection: ' + msg);
    console.error(e.reason);
});
</script>
<script type="importmap">
{
  "imports": {
    "three": "./libs/threejs/three.module.js",
    "three/addons/": "./libs/threejs/examples/jsm/",
    "meshopt_decoder": "./libs/meshoptimizer/meshopt_decoder.module.js"
  }
}
</script>
</head>
<body>
<div id="viewer"></div>
<div id="topbar">
    <div id="title">Offline Three.js GLB Viewer v12-BuildDepsDebug</div>
    <div id="status">等待选择 GLB。离线 Three.js / MeshoptDecoder / LocalHTTP。</div>
</div>
<div id="hint">左侧压缩完成后，点击列表文件即可预览<br>鼠标左键旋转，滚轮缩放，右键平移</div>
<div id="debuglog">debug log waiting...</div>

<script type="module">
window.__vlog('module script entered: dynamic-import mode');

let THREE, OrbitControls, GLTFLoader, MeshoptDecoder;
let scene, camera, renderer, controls, currentModel;

async function fetchCheck(name, url) {
    try {
        window.__vlog('preflight fetch: ' + name + ' => ' + url);
        const res = await fetch(url, { cache: 'no-store' });
        window.__vlog('preflight result: ' + name + ' HTTP ' + res.status + ' content-type=' + (res.headers.get('content-type') || ''));
        if (!res.ok) throw new Error(name + ' HTTP ' + res.status + ' ' + url);
        const text = await res.text();
        window.__vlog('preflight size: ' + name + ' bytes=' + text.length + ' first=' + text.slice(0, 60).replace(/\n/g, ' '));
        if (text.toLowerCase().includes('<html')) {
            throw new Error(name + ' 返回 HTML，不是 JS 文件：' + url);
        }
        return text;
    } catch (err) {
        window.__vlog('preflight failed: ' + name + ' => ' + (err && err.message ? err.message : String(err)));
        throw err;
    }
}


async function preflightRelativeImports(parentName, parentUrl, sourceText) {
    try {
        const baseUrl = new URL(parentUrl);
        const re = /(?:import|export)\s+(?:[^'"]*?\s+from\s+)?["'](\.\.?\/[^"']+)["']/g;
        const found = [];
        let m;
        while ((m = re.exec(sourceText)) !== null) {
            found.push(m[1]);
        }
        if (!found.length) {
            window.__vlog('dependency scan: ' + parentName + ' no relative imports found');
            return;
        }
        window.__vlog('dependency scan: ' + parentName + ' relative imports=' + found.join(', '));
        for (const rel of found) {
            const depUrl = new URL(rel, baseUrl).href;
            await fetchCheck('dependency ' + rel, depUrl);
        }
    } catch (err) {
        window.__vlog('dependency scan failed for ' + parentName + ': ' + (err && err.message ? err.message : String(err)));
        throw err;
    }
}

async function bootViewer() {
    const base = window.location.origin;
    const urls = {
        three: base + '/libs/threejs/three.module.js',
        orbit: base + '/libs/threejs/examples/jsm/controls/OrbitControls.js',
        gltf: base + '/libs/threejs/examples/jsm/loaders/GLTFLoader.js',
        meshopt: base + '/libs/threejs/examples/jsm/libs/meshopt_decoder.module.js'
    };

    window.__vlog('bootViewer base=' + base);

    const threeText = await fetchCheck('three.module.js', urls.three);
    await preflightRelativeImports('three.module.js', urls.three, threeText);
    await fetchCheck('OrbitControls.js', urls.orbit);
    await fetchCheck('GLTFLoader.js', urls.gltf);
    await fetchCheck('meshopt_decoder.module.js', urls.meshopt);

    try {
        window.__vlog('dynamic import three start');
        THREE = await import(urls.three);
        window.__vlog('dynamic import three OK, revision=' + (THREE.REVISION || 'unknown'));

        window.__vlog('dynamic import OrbitControls start');
        ({ OrbitControls } = await import(urls.orbit));
        window.__vlog('dynamic import OrbitControls OK');

        window.__vlog('dynamic import GLTFLoader start');
        ({ GLTFLoader } = await import(urls.gltf));
        window.__vlog('dynamic import GLTFLoader OK');

        window.__vlog('dynamic import MeshoptDecoder start');
        ({ MeshoptDecoder } = await import(urls.meshopt));
        window.__vlog('dynamic import MeshoptDecoder OK');
    } catch (err) {
        window.__vlog('dynamic import failed: ' + (err && err.stack ? err.stack : String(err)));
        const s = document.getElementById('status');
        if (s) s.textContent = '❌ ES Module 导入失败：' + (err && err.message ? err.message : String(err));
        throw err;
    }

    init();
    animate();
    window.__viewerReady = true;
    document.getElementById('status').textContent = 'Viewer 已就绪。Dynamic Import + LocalHTTP。';
    window.__vlog('viewer ready true');
    if (window.__pendingModelUrl) {
        const queued = window.__pendingModelUrl;
        window.__pendingModelUrl = null;
        window.loadModel(queued);
    }
}

function init() {
    window.__vlog('init: create scene/camera/renderer');
    const container = document.getElementById('viewer');
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x111827);

    camera = new THREE.PerspectiveCamera(50, window.innerWidth / window.innerHeight, 0.01, 100000);
    camera.position.set(3, 2, 5);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.0;
    container.appendChild(renderer.domElement);
    window.__vlog('WebGLRenderer created and canvas appended');

    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    scene.add(new THREE.HemisphereLight(0xffffff, 0x2f3542, 1.6));

    const dir1 = new THREE.DirectionalLight(0xffffff, 2.0);
    dir1.position.set(5, 8, 5);
    scene.add(dir1);

    const dir2 = new THREE.DirectionalLight(0xffffff, 0.8);
    dir2.position.set(-5, 2, -5);
    scene.add(dir2);

    const grid = new THREE.GridHelper(10, 20, 0x4b5563, 0x374151);
    grid.name = 'helper_grid';
    scene.add(grid);
    window.__vlog('grid added to scene');

    window.addEventListener('resize', onResize);
}

function onResize() {
    if (!camera || !renderer) return;
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
}

function disposeObject(obj) {
    obj.traverse((child) => {
        if (child.geometry) child.geometry.dispose();
        if (child.material) {
            const materials = Array.isArray(child.material) ? child.material : [child.material];
            materials.forEach((mat) => {
                for (const key in mat) {
                    const value = mat[key];
                    if (value && value.isTexture) value.dispose();
                }
                mat.dispose();
            });
        }
    });
}

function clearModel() {
    if (currentModel) {
        scene.remove(currentModel);
        disposeObject(currentModel);
        currentModel = null;
    }
}

function frameObject(object) {
    const box = new THREE.Box3().setFromObject(object);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());

    object.position.x += object.position.x - center.x;
    object.position.y += object.position.y - center.y;
    object.position.z += object.position.z - center.z;

    const maxDim = Math.max(size.x, size.y, size.z) || 1;
    const fov = camera.fov * (Math.PI / 180);
    let cameraZ = Math.abs(maxDim / 2 / Math.tan(fov / 2));
    cameraZ *= 1.8;

    camera.position.set(cameraZ, cameraZ * 0.65, cameraZ);
    camera.near = Math.max(cameraZ / 1000, 0.01);
    camera.far = cameraZ * 1000;
    camera.updateProjectionMatrix();

    controls.target.set(0, 0, 0);
    controls.update();
}

window.loadModel = async function(fileUrl) {
    window.__vlog('loadModel called: ' + fileUrl);
    if (!window.__viewerReady) {
        window.__pendingModelUrl = fileUrl;
        const s = document.getElementById('status');
        if (s) s.textContent = 'Viewer 初始化中，模型已排队...';
        return;
    }
    try {
        const hintEl = document.getElementById('hint');
        const titleEl = document.getElementById('title');
        const statusEl = document.getElementById('status');
        hintEl.style.display = 'none';
        titleEl.textContent = decodeURIComponent((fileUrl.split('path=')[1] || fileUrl).split('/').pop() || 'GLB Model');
        statusEl.textContent = '加载中...';

        clearModel();

        window.__vlog('waiting MeshoptDecoder.ready');
        await MeshoptDecoder.ready;
        window.__vlog('MeshoptDecoder ready');
        const loader = new GLTFLoader();
        loader.setMeshoptDecoder(MeshoptDecoder);

        loader.load(
            fileUrl,
            (gltf) => {
                window.__vlog('GLTFLoader success callback');
                currentModel = gltf.scene;
                scene.add(currentModel);
                frameObject(currentModel);
                statusEl.textContent = '✅ 加载成功。MeshoptDecoder 已启用。';
            },
            (xhr) => {
                if (xhr.total) {
                    const pct = Math.round(xhr.loaded / xhr.total * 100);
                    statusEl.textContent = `加载中... ${pct}%`;
                    if (pct % 25 === 0) window.__vlog('GLB loading ' + pct + '%');
                } else if (xhr.loaded) {
                    statusEl.textContent = `加载中... ${(xhr.loaded/1024/1024).toFixed(2)} MB`;
                }
            },
            (err) => {
                window.__vlog('GLTFLoader error: ' + (err && err.message ? err.message : String(err)));
                console.error(err);
                statusEl.textContent = '❌ 加载失败：' + (err && err.message ? err.message : '请确认文件是有效 GLB。');
                hintEl.style.display = 'block';
            }
        );
    } catch (err) {
        window.__vlog('loadModel catch: ' + (err && err.stack ? err.stack : String(err)));
        console.error(err);
        document.getElementById('status').textContent = '❌ Viewer 错误：' + err.message;
        document.getElementById('hint').style.display = 'block';
    }
};

function animate() {
    requestAnimationFrame(animate);
    if (controls) controls.update();
    if (renderer && scene && camera) renderer.render(scene, camera);
}

bootViewer().catch((err) => {
    window.__vlog('bootViewer failed: ' + (err && err.stack ? err.stack : String(err)));
    const s = document.getElementById('status');
    if (s) s.textContent = '❌ Viewer 启动失败：' + (err && err.message ? err.message : String(err));
});
</script>
</body>
</html>
"""
        # v7 fix: importmap must use URL style paths, not Windows backslashes.
        # Use absolute localhost URLs to avoid file://, backslash and missing-slash path bugs.
        base = self.base_url.rstrip("/")
        html = html.replace('"three": "./libs/threejs/three.module.js"', f'"three": "{base}/libs/threejs/three.module.js"')
        html = html.replace('"three/addons/": "./libs/threejs/examples/jsm/"', f'"three/addons/": "{base}/libs/threejs/examples/jsm/"')
        html = html.replace('"meshopt_decoder": "./libs/meshoptimizer/meshopt_decoder.module.js"', f'"meshopt_decoder": "{base}/libs/threejs/examples/jsm/libs/meshopt_decoder.module.js"')
        return html


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GLB 批次压缩 + 离线 Three.js 预览器 v12-BuildDepsDebug")
        self.resize(1400, 820)
        self.jobs = []
        self.worker = None
        self.download_worker = None
        self._build_ui()
        self._build_menu()

    def _build_menu(self):
        menu = self.menuBar().addMenu("文件")
        add_action = QAction("加入 GLB 文件", self)
        add_action.triggered.connect(self.add_files)
        menu.addAction(add_action)

        clear_action = QAction("清空列表", self)
        clear_action.triggered.connect(self.clear_jobs)
        menu.addAction(clear_action)

        menu.addSeparator()
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.close)
        menu.addAction(exit_action)

        tools = self.menuBar().addMenu("工具")
        download_action = QAction("从 CDN 下载离线 Three.js / MeshoptDecoder 文件", self)
        download_action.triggered.connect(self.download_offline_assets_from_cdn)
        tools.addAction(download_action)

        download_all_jsm_action = QAction("下载 Three.js 0.184.0 build + examples/jsm 全部文件", self)
        download_all_jsm_action.triggered.connect(self.download_three_184_all_jsm)
        tools.addAction(download_all_jsm_action)

        check_action = QAction("检查离线 Three.js 库", self)
        check_action.triggered.connect(self.check_offline_assets)
        tools.addAction(check_action)

        diag_action = QAction("Viewer 诊断 / 输出加载日志", self)
        diag_action.triggered.connect(self.viewer_diagnostics)
        tools.addAction(diag_action)

        tools.addSeparator()
        open_libs_action = QAction("打开 libs 文件夹", self)
        open_libs_action.triggered.connect(self.open_libs_folder)
        tools.addAction(open_libs_action)


    def _asset_paths(self):
        app_dir = Path(__file__).resolve().parent
        return {
            "three.module.js": app_dir / "libs" / "threejs" / "three.module.js",
            "three.core.js": app_dir / "libs" / "threejs" / "three.core.js",
            "OrbitControls.js": app_dir / "libs" / "threejs" / "examples" / "jsm" / "controls" / "OrbitControls.js",
            "GLTFLoader.js": app_dir / "libs" / "threejs" / "examples" / "jsm" / "loaders" / "GLTFLoader.js",
            "meshopt_decoder.module.js": app_dir / "libs" / "threejs" / "examples" / "jsm" / "libs" / "meshopt_decoder.module.js",
        }

    def _asset_urls(self):
        return {
            "three.module.js": "https://unpkg.com/three@0.184.0/build/three.module.js",
            "OrbitControls.js": "https://unpkg.com/three@0.184.0/examples/jsm/controls/OrbitControls.js",
            "GLTFLoader.js": "https://unpkg.com/three@0.184.0/examples/jsm/loaders/GLTFLoader.js",
            "meshopt_decoder.module.js": "https://unpkg.com/three@0.184.0/examples/jsm/libs/meshopt_decoder.module.js",
        }

    def download_offline_assets_from_cdn(self):
        reply = QMessageBox.question(
            self,
            "下载离线库",
            "将从 unpkg.com 下载 Three.js 0.184.0、GLTFLoader、OrbitControls 和 MeshoptDecoder。\n\n"
            "下载后会储存在本程序同目录的 libs 文件夹。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        urls = self._asset_urls()
        paths = self._asset_paths()
        ok = []
        failed = []
        ctx = ssl.create_default_context()

        self.append_log("\n[下载离线 Three.js / MeshoptDecoder 文件]")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for name, url in urls.items():
                target = paths[name]
                target.parent.mkdir(parents=True, exist_ok=True)
                self.append_log(f"下载：{url}")
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                        data = resp.read()
                    if len(data) < 1024:
                        raise RuntimeError(f"下载内容太小，可能不是有效 JS 文件：{len(data)} bytes")
                    target.write_bytes(data)
                    ok.append(f"✅ {name} -> {target}")
                    self.append_log(f"✅ 已保存：{target}")
                except Exception as exc:
                    failed.append(f"❌ {name}: {exc}")
                    self.append_log(f"❌ 下载失败：{name} | {exc}")

            # Compatibility copy: some old offline versions expect meshopt here.
            meshopt_src = paths["meshopt_decoder.module.js"]
            compat = Path(__file__).resolve().parent / "libs" / "meshoptimizer" / "meshopt_decoder.module.js"
            if meshopt_src.exists():
                compat.parent.mkdir(parents=True, exist_ok=True)
                if meshopt_src.resolve() != compat.resolve():
                    shutil.copy2(meshopt_src, compat)
                self.append_log(f"✅ 兼容复制：{compat}")

        finally:
            QApplication.restoreOverrideCursor()

        msg = "下载完成。" if not failed else "部分文件下载失败。"
        detail = "\n".join(ok + failed)
        QMessageBox.information(self, msg, detail or msg)
        self.check_offline_assets(show_success_only=False)

    def download_three_184_all_jsm(self):
        reply = QMessageBox.question(
            self,
            "下载完整 examples/jsm",
            "将从 UNPKG 下载 Three.js 0.184.0 的 examples/jsm 目录全部文件，"
            "并同时下载 build/ 目录全部依赖文件。\n\n"
            "文件会保存到：libs/threejs/examples/jsm/\n"
            "这个过程可能需要数分钟，取决于网络速度。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self.download_worker and self.download_worker.isRunning():
            QMessageBox.warning(self, "正在下载", "已有下载任务正在进行，请等待完成。")
            return

        app_dir = Path(__file__).resolve().parent
        self.download_worker = FullJsmDownloadWorker(app_dir, self)
        self.download_worker.log_message.connect(self.append_log)
        self.download_worker.progress_changed.connect(self.on_progress)
        self.download_worker.finished_download.connect(self.on_full_jsm_download_finished)
        self.progress.setValue(0)
        self.append_log("\n准备下载 Three.js 0.184.0 build + examples/jsm 全部文件...")
        self.download_worker.start()

    def on_full_jsm_download_finished(self, ok, failed, last_error):
        if failed:
            QMessageBox.warning(
                self,
                "下载完成但有失败",
                f"成功：{ok}\n失败：{failed}\n最后错误：{last_error or '-'}\n\n"
                "可以重新点击同一个菜单继续补下载。",
            )
        else:
            QMessageBox.information(
                self,
                "下载完成",
                f"Three.js 0.184.0 examples/jsm 全部文件已下载完成。\n成功文件数：{ok}",
            )
        self.check_offline_assets(show_success_only=False)

    def check_offline_assets(self, show_success_only=True):
        paths = self._asset_paths()
        missing = []
        existing = []
        for name, path in paths.items():
            if path.exists() and path.stat().st_size > 0:
                existing.append(f"✅ {name} ({path.stat().st_size:,} bytes)\n{path}")
            else:
                missing.append(f"❌ {name}\n{path}")

        if missing:
            QMessageBox.warning(
                self,
                "离线库不完整",
                "缺少以下文件：\n\n" + "\n\n".join(missing) +
                "\n\n请点击：工具 > 从 CDN 下载离线 Three.js / MeshoptDecoder 文件",
            )
        elif show_success_only:
            QMessageBox.information(self, "离线库完整", "所有离线 Three.js / MeshoptDecoder 文件都已存在。\n\n" + "\n\n".join(existing))
        else:
            self.append_log("✅ 离线库检查完成：所有文件齐全。")

    def open_libs_folder(self):
        libs = Path(__file__).resolve().parent / "libs"
        libs.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(libs))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(libs)])
            else:
                subprocess.Popen(["xdg-open", str(libs)])
        except Exception as exc:
            QMessageBox.warning(self, "无法打开文件夹", str(exc))

    def viewer_diagnostics(self):
        try:
            report = self.viewer.diagnostic_report()
            self.append_log(report)
            QMessageBox.information(self, "Viewer 诊断报告", report[:4000])
        except Exception as exc:
            self.append_log(f"❌ Viewer 诊断失败：{exc}")
            QMessageBox.warning(self, "Viewer 诊断失败", str(exc))

    def _build_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._left_panel())
        self.viewer = ThreeJsViewer()
        self.viewer.viewer_log.connect(self.append_log)
        splitter.addWidget(self.viewer)
        splitter.setSizes([560, 840])
        self.setCentralWidget(splitter)

    def _left_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel("批次 GLB Meshopt 压缩工具")
        title.setStyleSheet("font-size: 20px; font-weight: 800;")
        layout.addWidget(title)

        subtitle = QLabel("左侧批次压缩，右侧即时预览。完成后点击列表行可直接打开压缩后的 GLB。")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #666;")
        layout.addWidget(subtitle)

        file_box = QGroupBox("1. 文件")
        file_layout = QHBoxLayout(file_box)
        self.btn_add = QPushButton("加入多个 GLB")
        self.btn_add.clicked.connect(self.add_files)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self.clear_jobs)
        self.lbl_count = QLabel("0 个文件")
        file_layout.addWidget(self.btn_add)
        file_layout.addWidget(self.btn_clear)
        file_layout.addStretch(1)
        file_layout.addWidget(self.lbl_count)
        layout.addWidget(file_box)

        config_box = QGroupBox("2. 压缩模式")
        config_layout = QVBoxLayout(config_box)

        self.rb_meshopt = QRadioButton("低损几何压缩：Meshopt only，不减面")
        self.rb_simplify = QRadioButton("深度压缩：Simplify 减面 + Meshopt 二次压缩")
        self.rb_meshopt.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.rb_meshopt)
        self.mode_group.addButton(self.rb_simplify)
        self.rb_meshopt.toggled.connect(self._mode_changed)
        self.rb_simplify.toggled.connect(self._mode_changed)
        config_layout.addWidget(self.rb_meshopt)
        config_layout.addWidget(self.rb_simplify)

        ratio_row = QHBoxLayout()
        ratio_row.addWidget(QLabel("保留面数比例："))
        self.ratio_slider = QSlider(Qt.Orientation.Horizontal)
        self.ratio_slider.setRange(5, 95)
        self.ratio_slider.setSingleStep(5)
        self.ratio_slider.setValue(30)
        self.ratio_slider.valueChanged.connect(self._ratio_changed)
        self.lbl_ratio = QLabel("0.30")
        ratio_row.addWidget(self.ratio_slider, 1)
        ratio_row.addWidget(self.lbl_ratio)
        config_layout.addLayout(ratio_row)

        self.lbl_ratio_hint = QLabel("Meshopt only 模式不使用减面比例。")
        self.lbl_ratio_hint.setStyleSheet("color: #777;")
        config_layout.addWidget(self.lbl_ratio_hint)
        layout.addWidget(config_box)
        self._mode_changed()

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["原始文件", "状态", "原始 MB", "压缩 MB", "缩小 %", "输出文件"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.cellClicked.connect(self.preview_selected_row)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("开始批次压缩")
        self.btn_run.setMinimumHeight(38)
        self.btn_run.setStyleSheet("font-weight: 800;")
        self.btn_run.clicked.connect(self.start_compression)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setMinimumHeight(38)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_compression)
        btn_row.addWidget(self.btn_run, 1)
        btn_row.addWidget(self.btn_stop)
        layout.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(150)
        self.log.setStyleSheet("background: #111827; color: #d1d5db; font-family: Consolas, monospace;")
        layout.addWidget(self.log)

        return panel

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择一个或多个 GLB 文件",
            "",
            "GLB Files (*.glb);;All Files (*.*)",
        )
        if not files:
            return

        existing = {job.source_path for job in self.jobs}
        added = 0
        for src in files:
            if src in existing:
                continue
            path = Path(src)
            output = str(path.with_name(path.stem + "_compressed.glb"))
            self.jobs.append(FileJob(source_path=str(path), output_path=output))
            added += 1

        self.refresh_table()
        self.append_log(f"已加入 {added} 个文件。")

    def clear_jobs(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "处理中", "批次压缩正在进行，不能清空列表。")
            return
        self.jobs.clear()
        self.refresh_table()
        self.progress.setValue(0)
        self.append_log("已清空列表。")

    def _mode_changed(self):
        enabled = self.rb_simplify.isChecked()
        self.ratio_slider.setEnabled(enabled)
        self.lbl_ratio.setEnabled(enabled)
        if enabled:
            self.lbl_ratio_hint.setText("例如 0.30 = 保留约 30% 面数，适合大型模型瘦身。")
        else:
            self.lbl_ratio_hint.setText("Meshopt only 模式不使用减面比例，适合保留模型细节。")

    def _ratio_changed(self):
        self.lbl_ratio.setText(f"{self.ratio_slider.value() / 100:.2f}")

    def refresh_table(self):
        self.table.setRowCount(len(self.jobs))
        for row, job in enumerate(self.jobs):
            self._set_row(row, job)
        self.lbl_count.setText(f"{len(self.jobs)} 个文件")

    def _set_row(self, row, job):
        values = [
            os.path.basename(job.source_path),
            job.status,
            f"{job.original_mb:.2f}" if job.original_mb else "-",
            f"{job.compressed_mb:.2f}" if job.compressed_mb else "-",
            f"{job.reduction_pct:.1f}%" if job.compressed_mb else "-",
            os.path.basename(job.output_path),
        ]
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(job.output_path if col == 5 else job.source_path)
            if job.status == "完成":
                item.setBackground(QColor("#e8f5e9"))
            elif job.status == "失败":
                item.setBackground(QColor("#ffebee"))
                item.setToolTip(job.error)
            elif job.status == "处理中":
                item.setBackground(QColor("#fff8e1"))
            self.table.setItem(row, col, item)

    def update_row_from_worker(self, row, job):
        if 0 <= row < len(self.jobs):
            self.jobs[row] = job
            self._set_row(row, job)

    def start_compression(self):
        if not self.jobs:
            QMessageBox.warning(self, "没有文件", "请先加入至少一个 GLB 文件。")
            return

        mode = "simplify" if self.rb_simplify.isChecked() else "meshopt_only"
        ratio = self.ratio_slider.value() / 100

        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_add.setEnabled(False)
        self.btn_clear.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setMaximum(len(self.jobs))

        self.worker = CompressorWorker(self.jobs.copy(), mode, ratio, self)
        self.worker.log_message.connect(self.append_log)
        self.worker.progress_changed.connect(self.on_progress)
        self.worker.row_updated.connect(self.update_row_from_worker)
        self.worker.finished_all.connect(self.on_finished)
        self.worker.start()

    def stop_compression(self):
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            self.append_log("正在请求停止，请等待当前文件处理结束。")

    def on_progress(self, value, total):
        self.progress.setMaximum(total)
        self.progress.setValue(value)

    def on_finished(self):
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_add.setEnabled(True)
        self.btn_clear.setEnabled(True)
        self.progress.setValue(self.progress.maximum())
        QMessageBox.information(self, "完成", "批次处理结束。点击列表中的完成文件即可在右侧预览。")

    def preview_selected_row(self, row, col):
        if not (0 <= row < len(self.jobs)):
            return
        job = self.jobs[row]
        preview_path = job.output_path if os.path.exists(job.output_path) else job.source_path
        if not os.path.exists(preview_path):
            QMessageBox.warning(self, "文件不存在", "找不到可预览的 GLB 文件。")
            return
        self.append_log(f"预览：{preview_path}")
        self.viewer.load_glb(preview_path)

    def append_log(self, text):
        self.log.append(text)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
