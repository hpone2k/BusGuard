"""Small shared API for the passenger website and demonstration operator."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .assistance import AssistanceError

StopId = Literal['campus', 'interchange', 'community', 'stop_d', 'stop_e']


class AtStopLocation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['at_stop']
    stop_id: StopId


class OtherLocation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['onboard', 'away']


DemoLocation = Annotated[AtStopLocation | OtherLocation, Field(discriminator='mode')]


class AssistanceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_request_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')
    request_token: str = Field(min_length=24, max_length=128, pattern=r'^[A-Za-z0-9_-]+$')
    journey: Literal['boarding', 'alighting']
    stop_id: StopId
    # Old saved requests can still retry their exact identity. The controller
    # requires a location for every new request, including manual mode.
    location: DemoLocation | None = None
    needs: list[Literal['ramp', 'extra_time', 'audio', 'visual', 'priority_seat']] = Field(min_length=1, max_length=5)


class AssistanceControl(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['arrive', 'depart', 'obstruction', 'emergency', 'reset',
                    'secure_wheelchair', 'confirm_sensor_requests', 'cabin_coverage']
    stop_id: StopId | None = None
    value: bool | None = None


class ScenarioControl(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['start', 'stop', 'board', 'alight', 'rfid', 'load', 'disable', 'reset']
    stop_id: StopId | None = None
    seat_type: Literal['priority', 'standard'] | None = None
    event_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')
    priority_occupied: int | None = Field(default=None, ge=0, le=6, strict=True)
    standard_occupied: int | None = Field(default=None, ge=0, le=20, strict=True)
    priority_regular: int | None = Field(default=None, ge=0, le=6, strict=True)


def assistance_router(controller, bridge, operator_dependency):
    router = APIRouter(prefix='/api/assistance', tags=['assistance'])

    def invoke(method, *args, **kwargs):
        try:
            controller.tick(bridge.snapshot())
            return method(*args, **kwargs)
        except AssistanceError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from None
        except OSError:
            # Never report a passenger request as saved when the durable write failed.
            controller.fail_hold()
            raise HTTPException(status_code=503, detail='The request journal is unavailable. Assistance is held; contact the operator.') from None

    @router.get('/config')
    def config():
        return controller.config()

    @router.post('/requests')
    def create(payload: AssistanceRequest):
        return invoke(controller.create_request, payload.model_dump())

    @router.get('/requests/{request_id}')
    def get(request_id: str, x_request_token: str | None = Header(default=None, max_length=128)):
        return invoke(controller.get_request, request_id, x_request_token)

    @router.post('/requests/{request_id}/complete')
    def complete(request_id: str, x_request_token: str | None = Header(default=None, max_length=128)):
        return invoke(controller.complete_request, request_id, x_request_token)

    @router.post('/requests/{request_id}/cancel')
    def cancel(request_id: str, x_request_token: str | None = Header(default=None, max_length=128)):
        return invoke(controller.cancel_request, request_id, x_request_token)

    @router.get('/state')
    def state():
        return invoke(controller.snapshot)

    @router.post('/control', dependencies=[Depends(operator_dependency)])
    def control(payload: AssistanceControl):
        return invoke(controller.control, **payload.model_dump())

    @router.post('/scenario', dependencies=[Depends(operator_dependency)])
    def scenario(payload: ScenarioControl):
        return invoke(controller.scenario_control, **payload.model_dump(exclude_none=True))

    return router
