"""Strict local routes for Seller extensions; browser confirms saved plans only."""
from typing import Any
from pydantic import Field
from .models import StrictDTO
from .operation_routes import ConfirmRequest
from .ozon_write import WriteError
from .seller_extensions import SellerExtensions, StrategyOperation

class SellerRequest(StrictDTO):
    operation:str=Field(min_length=1,max_length=64)
    request:dict[str,Any]

def install_seller_extensions(app,accounts,reader,*,root):
    service=SellerExtensions(accounts,reader,root=root,verified_writes=StrategyOperation.__members__)
    app.state.seller_extensions=service
    @app.post('/api/seller/read')
    async def read(request:SellerRequest):
        return await service.read(request.operation,request.request)
    @app.post('/api/seller/preview')
    async def preview(request:SellerRequest):
        return await service.preview(request.operation,request.request)
    @app.post('/api/seller/execute')
    async def execute(request:ConfirmRequest):
        warehouse=getattr(app.state,'warehouse_reports',None)
        if warehouse is not None and warehouse.has_unresolved():
            raise WriteError('UNRESOLVED','Сначала сверьте незавершённый результат товаров, цен или отчётов',409)
        if app.state.mutations.has_unresolved():
            raise WriteError('UNRESOLVED','Сначала сверьте неизвестный результат цен, остатков или акции',409)
        return await service.execute(request.plan_id,request.plan_hash,request.confirmed)
    @app.post('/api/seller/reconcile')
    async def reconcile(request:ConfirmRequest):
        return await service.reconcile(request.plan_id,request.plan_hash)
    @app.get('/api/seller/history')
    def history():
        return service.history()
