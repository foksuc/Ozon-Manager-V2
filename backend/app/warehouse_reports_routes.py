"""Local request DTOs never accept an arbitrary URL or mutable execution body."""
from typing import Any
from pydantic import Field
from .models import StrictDTO
from .operation_routes import ConfirmRequest
from .warehouse_reports import WarehouseReports, WarehouseWrite, CONTRACTS, LIVE_BLOCKED

class WarehouseRequest(StrictDTO):
    operation:str=Field(min_length=1,max_length=64)
    request:dict[str,Any]

def install_warehouse_reports(app,accounts,reader,*,root,blocked=lambda:False):
    service=WarehouseReports(accounts,reader,root=root,blocked=blocked,verified_writes=WarehouseWrite.__members__)
    app.state.warehouse_reports=service
    @app.get('/api/warehouse/contracts')
    def contracts():
        return {'operations':CONTRACTS,'live_blocked':LIVE_BLOCKED,'max_plan_items':100}
    @app.post('/api/warehouse/read')
    async def read(request:WarehouseRequest):return await service.read(request.operation,request.request)
    @app.post('/api/warehouse/preview')
    async def preview(request:WarehouseRequest):return await service.preview(request.operation,request.request)
    @app.post('/api/warehouse/execute')
    async def execute(request:ConfirmRequest):return await service.execute(request.plan_id,request.plan_hash,request.confirmed)
    @app.post('/api/warehouse/reconcile')
    async def reconcile(request:ConfirmRequest):return await service.reconcile(request.plan_id,request.plan_hash)
    @app.get('/api/warehouse/history')
    def history():return service.history()
