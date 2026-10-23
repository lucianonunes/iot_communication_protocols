.PHONY: help install lint typecheck test check up down logs clean

help:  ## Lista os alvos disponíveis
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install:  ## Instala dependências e hooks de pre-commit
	uv sync
	uv run pre-commit install

lint:  ## Formatação e lint (ruff)
	uv run ruff format --check src tests
	uv run ruff check src tests

typecheck:  ## Verificação de tipos (mypy strict)
	uv run mypy

test:  ## Testes unitários
	uv run pytest

check: lint typecheck test  ## Todas as verificações

.env:
	cp .env.example .env

up: .env  ## Sobe a pilha (simuladores, servidores, InfluxDB, Grafana)
	mkdir -p data
	docker compose up -d --build

down:  ## Para a pilha (mantém os volumes)
	docker compose down

logs:  ## Acompanha os logs
	docker compose logs -f --tail 50

clean:  ## Para a pilha e apaga volumes e dados brutos
	docker compose down -v
	rm -rf data
