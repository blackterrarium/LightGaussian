#!/bin/bash

source build_base.sh

IMAGE=${IMAGE_NAME}
CONTAINER=${CONTAINER_NAME}
echo ${CONTAINER}
echo ${IMAGE}
    
docker run --name ${CONTAINER} -it -e NVIDIA_DRIVER_CAPABILITIES=all \
    --gpus all --ipc=host --ulimit memlock=-1 --ulimit stack=67108864 \
    --runtime nvidia --network host --privileged \
    --rm \
    -e DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix \
    -p 5000-8000:5000-8000 \
    -p 2022:22 \
    -v `pwd`/..:/app ${IMAGE} bash
    
# docker run --name ${CONTAINER} -it -e NVIDIA_DRIVER_CAPABILITIES=all \
#     --gpus all --ipc=host --ulimit memlock=-1 --ulimit stack=67108864 \
#     --runtime nvidia --network host --privileged \
#     --rm \
#     -e DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix \
#     -p 5000-8000:5000-8000 \
#     -p 2022:22 \
#     -v `pwd`/..:/app ${IMAGE} bash

# docker run -it \
#     --gpus all --ipc=host --ulimit memlock=-1 --ulimit stack=67108864 \
#     --runtime nvidia --network host \
#     -v `pwd`/..:/app ${IMAGE_NAME}:${VERSION} bash

