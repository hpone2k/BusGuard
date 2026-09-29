"""RFID operator management and least-privilege ESP32 reader ingress."""
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .assistance import AssistanceError
from .config import ROOT


class CardProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    passenger_type: Literal['normal', 'senior', 'pregnant', 'custom']
    custom_label: str = Field(default='', max_length=60)
    priority: bool = False
    extra_time: bool = False


class ReaderSetup(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reader_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    label: str = Field(default='ESP32 RC522', min_length=1, max_length=60)


class OperatorTap(BaseModel):
    model_config = ConfigDict(extra='forbid')
    uid: str = Field(min_length=8, max_length=29)
    event_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')
    source: Literal['simulation', 'keyboard'] = 'simulation'


class ReaderTap(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reader_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    uid: str = Field(min_length=8, max_length=29)
    event_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')
    window_id: str | None = Field(max_length=80)
    observed_at_ms: int = Field(ge=0, strict=True)


def rfid_router(controller, bridge, operator_dependency):
    router = APIRouter(prefix='/api/rfid', tags=['rfid'])

    def invoke(action, **kwargs):
        try:
            # Only accepted readers/operators reach this method. Fresh camera
            # reconciliation is applied before the card updates the same ledger.
            if action in {'tap', 'reader_state'}:
                with controller.lock:
                    controller.rfid.authenticate(kwargs.get('reader_id'), kwargs.get('authorization'))
            controller.tick(bridge.snapshot())
            return controller.rfid_call(action, **kwargs)
        except AssistanceError as exc:
            raise HTTPException(exc.status, str(exc)) from None
        except OSError:
            controller.fail_hold()
            raise HTTPException(503, 'The RFID journal could not be saved. The tap was not confirmed; the bus is held.') from None

    @router.get('/cards', dependencies=[Depends(operator_dependency)])
    def cards():
        return invoke('snapshot')

    @router.put('/cards/{card_id}', dependencies=[Depends(operator_dependency)])
    def update_card(card_id: str, payload: CardProfile):
        return invoke('update_card', card_id=card_id, **payload.model_dump())

    @router.post('/readers', dependencies=[Depends(operator_dependency)])
    def provision(payload: ReaderSetup):
        return invoke('provision', **payload.model_dump())

    @router.delete('/readers/{reader_id}', dependencies=[Depends(operator_dependency)])
    def revoke(reader_id: str):
        return invoke('revoke', reader_id=reader_id)

    @router.post('/test-tap', dependencies=[Depends(operator_dependency)])
    def test_tap(payload: OperatorTap):
        return invoke('test_tap', **payload.model_dump())

    @router.get('/reader-state')
    def reader_state(reader_id: str = Query(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$'),
                     authorization: str | None = Header(default=None, max_length=160)):
        return invoke('reader_state', reader_id=reader_id, authorization=authorization)

    @router.post('/tap')
    def tap(payload: ReaderTap, authorization: str | None = Header(default=None, max_length=160)):
        return invoke('tap', authorization=authorization, source='hardware', **payload.model_dump())

    @router.get('/firmware/{filename}', dependencies=[Depends(operator_dependency)])
    def firmware(filename: str):
        filenames = {'BusGuardRFID.ino', 'config.example.h', 'README.md'}
        if filename not in filenames:
            raise HTTPException(404, 'Firmware file not available.')
        path = ROOT / 'firmware' / 'BusGuardRFID' / filename
        if not path.is_file():
            raise HTTPException(404, 'Firmware file not available.')
        return FileResponse(path, filename=filename, media_type='text/plain')

    return router
