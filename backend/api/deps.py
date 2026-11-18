"""Dependencias FastAPI: servicos vivem em app.state (mesmo padrao de um projeto anterior do autor)."""

from fastapi import Request

from backend.core.config import Settings
from backend.generation.base import ImageProvider
from backend.jobs.manager import GenerationJobManager
from backend.storage.gallery import GenerationRepository
from backend.storage.projects import ProjectRepository
from backend.storage.uploads import ReferenceStore


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_repo(request: Request) -> GenerationRepository:
    return request.app.state.repo


def get_projects(request: Request) -> ProjectRepository:
    return request.app.state.projects


def get_uploads(request: Request) -> ReferenceStore:
    return request.app.state.uploads


def get_jobs(request: Request) -> GenerationJobManager:
    return request.app.state.jobs


def get_provider(request: Request) -> ImageProvider:
    return request.app.state.provider
