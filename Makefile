# Makefile for Pharma AI Platform (Podman Compose / Docker Compose compatible)
COMPOSE ?= $(shell if command -v podman >/dev/null 2>&1; then echo "podman compose"; else echo "docker compose"; fi)

.PHONY: up down logs ps test lint env

env:
	@if [ ! -f .env ]; then cp .env.example .env && echo "Created .env from .env.example"; fi

up: env
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

test:
	pytest -v

lint:
	ruff check src tests
	ruff format --check src tests
