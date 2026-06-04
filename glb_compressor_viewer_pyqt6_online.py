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
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
except Exception as exc:  # pragma: no cover
    QWebEngineView = None
    QWebEngineSettings = None
    WEBENGINE_IMPORT_ERROR = exc
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


class ThreeJsViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

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

        self.web = QWebEngineView(self)
        settings = self.web.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
        layout.addWidget(self.web)

        self.web.setHtml(self._viewer_html(), QUrl.fromLocalFile(os.getcwd() + os.sep))

    def load_glb(self, file_path):
        if not self.web:
            return
        file_url = QUrl.fromLocalFile(os.path.abspath(file_path)).toString()
        js = f"window.loadModel({file_url!r});"
        self.web.page().runJavaScript(js)

    def _viewer_html(self):
        return r"""
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
<script type="importmap">
{
  "imports": {
    "three": "https://unpkg.com/three@0.160.0/build/three.module.js",
    "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/",
    "meshopt_decoder": "https://unpkg.com/meshoptimizer@0.20.0/meshopt_decoder.module.js"
  }
}
</script>
</head>
<body>
<div id="viewer"></div>
<div id="topbar">
    <div id="title">Three.js GLB Viewer</div>
    <div id="status">等待选择 GLB。支持 EXT_meshopt_compression / MeshoptDecoder。</div>
</div>
<div id="hint">左侧压缩完成后，点击列表文件即可预览<br>鼠标左键旋转，滚轮缩放，右键平移</div>

<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'meshopt_decoder';

const container = document.getElementById('viewer');
const titleEl = document.getElementById('title');
const statusEl = document.getElementById('status');
const hintEl = document.getElementById('hint');

let scene, camera, renderer, controls, currentModel;

init();
animate();

function init() {
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

    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    const hemi = new THREE.HemisphereLight(0xffffff, 0x2f3542, 1.6);
    scene.add(hemi);

    const dir1 = new THREE.DirectionalLight(0xffffff, 2.0);
    dir1.position.set(5, 8, 5);
    scene.add(dir1);

    const dir2 = new THREE.DirectionalLight(0xffffff, 0.8);
    dir2.position.set(-5, 2, -5);
    scene.add(dir2);

    const grid = new THREE.GridHelper(10, 20, 0x4b5563, 0x374151);
    grid.name = 'helper_grid';
    scene.add(grid);

    window.addEventListener('resize', onResize);
}

function onResize() {
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
    try {
        hintEl.style.display = 'none';
        titleEl.textContent = decodeURIComponent(fileUrl.split('/').pop() || 'GLB Model');
        statusEl.textContent = '加载中...';

        clearModel();

        await MeshoptDecoder.ready;
        const loader = new GLTFLoader();
        loader.setMeshoptDecoder(MeshoptDecoder);

        loader.load(
            fileUrl,
            (gltf) => {
                currentModel = gltf.scene;
                scene.add(currentModel);
                frameObject(currentModel);
                statusEl.textContent = '✅ 加载成功。MeshoptDecoder 已启用。';
            },
            (xhr) => {
                if (xhr.total) {
                    const pct = Math.round(xhr.loaded / xhr.total * 100);
                    statusEl.textContent = `加载中... ${pct}%`;
                }
            },
            (err) => {
                console.error(err);
                statusEl.textContent = '❌ 加载失败：请确认文件是有效 GLB，或网络可访问 Three.js CDN。';
                hintEl.style.display = 'block';
            }
        );
    } catch (err) {
        console.error(err);
        statusEl.textContent = '❌ Viewer 错误：' + err.message;
        hintEl.style.display = 'block';
    }
}

function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
}
</script>
</body>
</html>
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GLB 批次压缩 + Meshopt Three.js 预览器 - PyQt6 整合版")
        self.resize(1400, 820)
        self.jobs = []
        self.worker = None
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

    def _build_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._left_panel())
        self.viewer = ThreeJsViewer()
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
