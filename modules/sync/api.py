from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from modules.catalog.importer import MasterMdbError

from .android import FetchError
from .models import DeviceRequest, MdbPathRequest
from .service import SyncService

logger = logging.getLogger(__name__)


def build_router(service: SyncService) -> APIRouter:
    router = APIRouter(tags=["sync"])

    @router.get("/status")
    def status() -> dict:
        return service.status()

    @router.get("/history")
    def history(limit: int = Query(default=20, ge=1, le=200)) -> list[dict]:
        return service.history(limit)

    @router.get("/device")
    def device() -> dict:
        return service.device_ready()

    @router.get("/device/check")
    def check_device(serial: str | None = None, package: str | None = None) -> dict:
        try:
            return service.check_device(serial=serial, package=package)
        except FetchError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/android")
    def apply_device(payload: DeviceRequest) -> dict:
        try:
            return service.apply_device(
                serial=payload.serial,
                package=payload.package,
                force=payload.force,
            )
        except FetchError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except MasterMdbError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/mdb")
    def apply_path(payload: MdbPathRequest) -> dict:
        try:
            return service.apply_path(
                payload.path,
                game_version=payload.game_version,
                data_version=payload.data_version,
                via="path",
                force=payload.force,
            )
        except MasterMdbError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/upload")
    async def apply_upload(
        file: Annotated[UploadFile, File()],
        game_version: Annotated[str | None, Form()] = None,
        data_version: Annotated[str | None, Form()] = None,
        force: Annotated[bool, Form()] = False,
    ) -> dict:
        max_bytes = service.options.upload_max_mb * 1024 * 1024
        filename = file.filename or "master.mdb"
        try:
            data = await file.read()
        finally:
            await file.close()

        if len(data) > max_bytes:
            limit = service.options.upload_max_mb
            raise HTTPException(
                status_code=413,
                detail=f"{filename} 超过 {limit} MB，请改用「从本机客户端提取」。",
            )
        try:
            return service.apply_upload(
                data,
                filename,
                game_version=game_version or None,
                data_version=data_version or None,
                force=force,
            )
        except MasterMdbError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
