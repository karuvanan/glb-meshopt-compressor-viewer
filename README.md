\# GLB Meshopt Batch Compressor + Offline Three.js Viewer



A PyQt6 desktop app for batch GLB Meshopt compression and offline Three.js preview with MeshoptDecoder support.



\## Features



\- Batch compress multiple GLB files.

\- Meshopt compression.

\- Simplify + Meshopt mode.

\- Embedded Three.js viewer.

\- Offline Three.js library downloader.

\- MeshoptDecoder support.

\- Local HTTP viewer for reliable offline loading.



\## Installation



```bash

npm init -y

npm install three meshoptimizer

pip install -r requirements.txt

npm install --global @gltf-transform/cli

python main.py


\## Offline Three.js Setup

Open the app and click:

Tools > Download Three.js 0.184.0 build + examples/jsm files

Then click:

Tools > Check offline Three.js library
Online Version
python glb_compressor_viewer_pyqt6_online.py

The online version requires internet connection because it loads Three.js from CDN.