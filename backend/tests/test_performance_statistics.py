import asyncio
import io
import json
import zipfile
from datetime import datetime,timezone
import httpx
import pytest
from app.performance_statistics import PerformanceStatisticsReader, PerformanceStatisticsQuery, request
from app.ozon_read import ReadError

def test_strict_contract():
    with pytest.raises(ValueError):PerformanceStatisticsQuery(operation='activate')
    with pytest.raises(ValueError):PerformanceStatisticsQuery(operation='daily',url='https://evil.invalid')
    with pytest.raises(ValueError):request(PerformanceStatisticsQuery(operation='status',uuid='../token'))
    with pytest.raises(ValueError):request(PerformanceStatisticsQuery(operation='daily',date_from='2026-01-01',date_to='2026-04-01'))

def test_wire_selectors_and_period():
    q=PerformanceStatisticsQuery(operation='daily',date_from='2026-10-01',date_to='2026-10-02',campaign_ids=['101','102'])
    assert request(q)==('GET','/api/client/statistics/daily/json',{'dateFrom':'2026-10-01','dateTo':'2026-10-02','campaignIds':['101','102']})
    q=q.model_copy(update={'operation':'all_orders','campaign_ids':[]})
    assert request(q)[2]=={'timeBounds.from':'2026-10-01T00:00:00Z','timeBounds.to':'2026-10-02T23:59:59Z'}

def test_mock_never_credentials_or_transport():
    async def run():
        reader=PerformanceStatisticsReader('MOCK',transport=httpx.MockTransport(lambda r:pytest.fail('network')))
        reader.credentials=lambda:pytest.fail('credentials')
        result=await reader.load(PerformanceStatisticsQuery(operation='campaign_report',date_from='2026-10-01',date_to='2026-10-02',campaign_ids=['101'],confirmed=True))
        assert result['data']['UUID'] and not result['complete']
        await reader.close()
    asyncio.run(run())

def test_unknown_generation_no_retry(tmp_path):
    async def run():
        calls=[]
        def transport(r):
            calls.append(r.url.path)
            if r.url.path.endswith('/token'):return httpx.Response(200,json={'access_token':'synthetic','expires_in':1800})
            raise httpx.ReadTimeout('synthetic')
        reader=PerformanceStatisticsReader('LIVE_READ',transport=httpx.MockTransport(transport),marker_path=tmp_path/'marker.json',root=tmp_path)
        reader.credentials=lambda:('synthetic','synthetic')
        q=PerformanceStatisticsQuery(operation='all_orders',date_from='2026-10-01',date_to='2026-10-02',confirmed=True)
        with pytest.raises(ReadError) as exc:await reader.load(q)
        assert exc.value.code=='UNKNOWN_REPORT_RESULT'
        with pytest.raises(ReadError) as exc:await reader.load(q)
        assert exc.value.code=='REPORT_RECONCILE'
        assert len(calls)==2
        await reader.close()
    asyncio.run(run())

def test_recovered_requires_explicit_terminal_reconcile(tmp_path):
    report='00000000-0000-4000-8000-000000000002'
    (tmp_path/'marker.json').write_text(json.dumps({'UUID':report,'status':'PENDING'}))
    async def run():
        def transport(r):return httpx.Response(200,json={'UUID':report,'state':'OK'})
        reader=PerformanceStatisticsReader('LIVE_READ',transport=httpx.MockTransport(transport),marker_path=tmp_path/'marker.json',root=tmp_path)
        reader.token='synthetic';reader.expires=999999999999
        await reader.load(PerformanceStatisticsQuery(operation='status',uuid=report))
        assert reader.active_uuid==report
        await reader.load(PerformanceStatisticsQuery(operation='status',uuid=report,reconcile=True))
        assert reader.active_uuid is None
        assert json.loads((tmp_path/'marker.json').read_text())=={'UUID':report,'status':'RESOLVED'}
        await reader.close()
    asyncio.run(run())

def test_vendor_and_irrelevant_fields():
    assert request(PerformanceStatisticsQuery(operation='vendor_report',date_from='2026-07-01',date_to='2026-10-01'))[2]['type']=='TRAFFIC_SOURCES'
    with pytest.raises(ValueError):request(PerformanceStatisticsQuery(operation='campaigns',date_from='2026-10-01'))
    assert PerformanceStatisticsReader.decode(b'{"amount":1.23}','daily')['amount']=='1.23'

def test_all_request_mappings():
    from app.performance_statistics import METHODS, GENERATE
    assert len(METHODS)==22
    for meta in METHODS:
        op=meta['id'];values={'operation':op}
        names={f['name'] for f in meta['fields']}
        if 'date_from' in names:values.update(date_from=datetime.now(timezone.utc).date().isoformat(),date_to=datetime.now(timezone.utc).date().isoformat())
        if 'campaign_ids' in names:values['campaign_ids']=['101']
        if 'uuid' in names:values['uuid']='00000000-0000-4000-8000-000000000003'
        if 'org_id' in names:values['org_id']='101'
        method,path,body=request(PerformanceStatisticsQuery(**values))
        assert method in ('GET','POST') and path.startswith('/api/client/') and isinstance(body,dict)
        if op in GENERATE and op!='vendor_report':
            values['format']='json'
            assert request(PerformanceStatisticsQuery(**values))[1]==path+'/json'

def test_mock_duplicate_guard_and_all_operations():
    from app.performance_statistics import METHODS, GENERATE
    async def run():
        for meta in METHODS:
            reader=PerformanceStatisticsReader('MOCK',transport=httpx.MockTransport(lambda r:pytest.fail('network')))
            reader.credentials=lambda:pytest.fail('secrets')
            names={f['name'] for f in meta['fields']};v={'operation':meta['id']}
            if 'date_from' in names:v.update(date_from=datetime.now(timezone.utc).date().isoformat(),date_to=datetime.now(timezone.utc).date().isoformat())
            if 'campaign_ids' in names:v['campaign_ids']=['101']
            if 'uuid' in names:v['uuid']='00000000-0000-4000-8000-000000000003'
            if 'org_id' in names:v['org_id']='101'
            if meta['id'] in GENERATE:v['confirmed']=True
            q=PerformanceStatisticsQuery(**v)
            result=await reader.load(q)
            assert result['mode']=='MOCK'
            if meta['id'] in GENERATE:
                with pytest.raises(ReadError):await reader.load(q)
                await reader.load(PerformanceStatisticsQuery(operation='status',uuid=result['data']['UUID']))
                with pytest.raises(ReadError):await reader.load(q)
            await reader.close()
    asyncio.run(run())

def test_json_shapes_and_nonfinite():
    for value,op in [(b'{"amount":NaN}','daily'),(b'{"amount":Infinity}','expense'),(b'{"error":"synthetic"}','daily'),(b'{"rows":{}}','sku'),(b'{"total":"1"}','reports'),(b'{}','campaigns')]:
        with pytest.raises(ValueError):PerformanceStatisticsReader.decode(value,op)
    assert PerformanceStatisticsReader.decode(b'{"id":9007199254740993}','daily')['id']=='9007199254740993'

def test_generation_http_error_never_retries(tmp_path):
    async def run():
        calls=[]
        def transport(r):calls.append(r);return httpx.Response(429,json={'error':'synthetic'})
        reader=PerformanceStatisticsReader('LIVE_READ',transport=httpx.MockTransport(transport),marker_path=tmp_path/'error-marker.json',root=tmp_path)
        reader.token='synthetic';reader.expires=999999999999
        q=PerformanceStatisticsQuery(operation='orders',date_from='2026-10-13',date_to='2026-10-13',confirmed=True)
        with pytest.raises(ReadError) as exc:await reader.load(q)
        assert exc.value.code=='UNKNOWN_REPORT_RESULT'
        with pytest.raises(ReadError):await reader.load(q)
        assert len(calls)==1
        await reader.close()
    asyncio.run(run())

def test_zip_no_traversal():
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:z.writestr('../101.csv','synthetic')
    with pytest.raises(ValueError):PerformanceStatisticsReader.decode(stream.getvalue(),'download')
