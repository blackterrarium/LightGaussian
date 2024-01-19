#!/bin/bash

source build_base.sh
docker build -t ${BASE_IMAGE} --platform linux/amd64 -f Dockerfile-amd64 .

docker run --name ${CONTAINER_NAME} -it -e NVIDIA_DRIVER_CAPABILITIES=all \
    --gpus all --ipc=host --ulimit memlock=-1 --ulimit stack=67108864 \
    --runtime nvidia --network host --privileged \
    -e DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix \
    -v `pwd`/..:/app ${BASE_IMAGE} bash -c "cd /app/docker; ./install_app.sh;"
docker commit ${CONTAINER_NAME} ${IMAGE_NAME}
docker container rm ${CONTAINER_NAME}


 