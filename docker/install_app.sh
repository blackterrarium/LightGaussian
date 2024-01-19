#!/bin/bash

set HOME_DIR="/app"
cd /app/submodules/compress-diff-gaussian-rasterization
pip install .

cd /app/submodules/simple-knn
pip install .