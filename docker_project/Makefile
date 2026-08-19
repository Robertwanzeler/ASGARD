.PHONY: help build run shell configure build-ns3 test stop clean restart

PROJECT_ROOT := $(abspath $(CURDIR)/..)
IMAGE := ns3-debug:latest
CONTAINER := ns3-debug

help:
	@echo "ns-3 Docker debug environment"
	@echo "  make build       Build the image"
	@echo "  make run         Start the container"
	@echo "  make shell       Open a shell with GDB available"
	@echo "  make configure   Configure ns-3 in debug mode"
	@echo "  make build-ns3   Compile ns-3"
	@echo "  make test        Run the ns-3 test suite"
	@echo "  make stop        Stop the container"
	@echo "  make clean       Remove the container and image"

build:
	docker build -f $(PROJECT_ROOT)/docker_project/Dockerfile -t $(IMAGE) $(PROJECT_ROOT)

run:
	@docker rm -f $(CONTAINER) 2>/dev/null || true
	docker run -d --name $(CONTAINER) \
		--cap-add=SYS_PTRACE --security-opt seccomp=unconfined \
		-v $(PROJECT_ROOT)/ns3-base:/workspace/ns3 \
		-v $(PROJECT_ROOT)/docker_project/debug_ns3:/workspace/debug \
		$(IMAGE)

shell:
	docker exec -it $(CONTAINER) bash

configure:
	docker exec $(CONTAINER) bash -lc 'cd /workspace/ns3 && ./ns3 configure --build-profile=debug --enable-examples --enable-tests'

build-ns3:
	docker exec $(CONTAINER) bash -lc 'cd /workspace/ns3 && ./ns3 build'

test:
	docker exec $(CONTAINER) bash -lc 'cd /workspace/ns3 && ./test.py'

stop:
	docker stop $(CONTAINER) 2>/dev/null || true

clean:
	docker rm -f $(CONTAINER) 2>/dev/null || true
	docker rmi $(IMAGE) 2>/dev/null || true

restart: stop run
