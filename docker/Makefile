tag_version := dev

GHCR_PAT ?= your_ghcr_token_here

# Default action, show info message
.PHONY: info
info:
	@echo "This Makefile is used for debug purposes. The image is built and pushed by the GitHub workflow."
	@echo "Available targets:"
	@echo "  build-image - Build the container image"
	@echo "  push-image - Push the image to GHCR"
	@echo "  push - Build and push the image to GHCR"

# Build the container image
# Context is the repo root (..) since the Dockerfile COPYs files that live
# there (requirements.txt, bot.py), not inside docker/.
.PHONY: build-image
build-image:
	docker build .. -f Dockerfile --pull \
		--platform=linux/amd64 \
		-t ghcr.io/zenokin/birb-discord:$(tag_version)

# Push image to GHCR
.PHONY: push-image
push-image:
	echo "$GHCR_PAT" | docker login ghcr.io -u "$GITHUB_ACTOR" --password-stdin
	docker push ghcr.io/zenokin/birb-discord:$(tag_version)

# Combined flow to build and push the image
.PHONY: push
push: build-image push-image
