# GLB Meshopt Batch Compressor + Offline Three.js Viewer

A PyQt6 desktop app for batch GLB Meshopt compression and offline Three.js preview with MeshoptDecoder support.

## Features

- Batch compress multiple GLB files.
- Meshopt compression.
- Simplify + Meshopt mode.
- Embedded Three.js viewer.
- Offline Three.js library downloader.
- MeshoptDecoder support.
- Local HTTP viewer for reliable offline loading.

## Requirements

- Python 3.10 or above
- Node.js and npm
- PyQt6
- PyQt6-WebEngine
- glTF Transform CLI

## Installation

Install Python libraries:

```bash
pip install -r requirements.txt
```

## Install glTF Transform CLI:
```bash
npm install --global @gltf-transform/cli
```

## Run Offline Version
```bash
python main.py
```

## After opening the app, click:
```text
Tools > Download Three.js 0.184.0 build + examples/jsm files

Then click:
Tools > Check offline Three.js library
```

## Run Online Version
```bash
python glb_compressor_viewer_pyqt6_online.py
```
The online version requires internet connection because it loads Three.js from CDN.


## Optional Manual Node Setup
```bash
npm init -y
npm install three meshoptimizer
```

## Output
Compressed files will be saved next to the original GLB file:
```text
model.glb
model_compressed.glb
```

## Controls
```text
Left mouse drag   Rotate
Mouse wheel       Zoom
Right mouse drag  Pan
```

## Offline Ready Package

The source repository does not include the full `libs/` folder to keep the repository lightweight.

If you want a ready-to-use offline package, download it from:

```text
GitHub Releases > Offline Ready Package
```

## The release zip includes:
```text
main.py
requirements.txt
README.md
LICENSE
libs/
```
## After extracting the zip, run:
```text
pip install -r requirements.txt
python main.py
```

## License
MIT License



