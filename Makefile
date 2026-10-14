# Makefile for Kick Prediction Backend

.PHONY: help up down logs shell test lint format migrate clean

# Default target
help:
	@echo "Available commands:"
	@echo "  up          - Start all services with docker-compose"
	@echo "  down        - Stop all services"
	@echo "  logs        - Show logs from all services"
	@echo "  shell       - Start interactive shell in API container"
	@echo "  test        - Run tests"
	@echo "  lint        - Run linting (ruff + mypy)"
	@echo "  format      - Format code with ruff"
	@echo "  migrate     - Run database migrations"
	@echo "  clean       - Clean up containers and volumes"

up:
	docker-compose up -d

down:
	docker-compose down

logs:
	docker-compose logs -f

shell:
	docker-compose exec api /bin/bash

test:
	docker-compose exec api pytest

lint:
	docker-compose exec api ruff check .
	docker-compose exec api mypy .

format:
	docker-compose exec api ruff format .

migrate:
	docker-compose exec api alembic upgrade head

clean:
	docker-compose down -v --remove-orphans
	docker system prune -f