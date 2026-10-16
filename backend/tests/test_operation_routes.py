import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import Store


@pytest.fixture
def api(tmp_path):
    app=create_app(Store(tmp_path/'mock.sqlite3',root=tmp_path))
    with TestClient(app,base_url='http://127.0.0.1:8000') as client:
        yield client


def payload(operation):
    stocks=operation=='stocks'
    auto=operation.startswith('auto_')
    add=operation.endswith('add')
    return {'operation':operation,'request':{'kind':'stocks' if stocks else 'prices',
        'ids':['mock-stock-1'] if stocks else [f'mock-product-{1 if auto else 4}-{2 if add else 1}'],
        'action_id':None if stocks else f'mock-action-{1 if auto else 4}',
        'role':'candidate' if add else 'member','auto_add':auto,
        'auto_add_date':'2026-10-01T00:00:00Z' if auto else None,
        'mode':'manual','value':'20' if stocks else '1000','rounding':'1','scenario':'ready'}}


def test_fractional_price_cannot_be_hidden_by_display_rounding(api):
    request=payload('prices')
    request['request'].update(value='1000.50',rounding='0.01')
    response=api.post('/api/mutations/preview',json=request)
    assert response.status_code==422
    assert 'целым' in response.json()['detail']


@pytest.mark.parametrize('operation',['prices','stocks','action_add','action_remove','auto_add','auto_remove'])
def test_manual_cycle_every_operation_mock(api,operation):
    prepared=api.post('/api/mutations/preview',json=payload(operation))
    assert prepared.status_code==200,prepared.text
    plan=prepared.json()
    assert plan['valid'] and plan['mode']=='MOCK'
    confirm={'plan_id':plan['id'],'plan_hash':plan['hash'],'confirmed':True}
    assert api.post('/api/mutations/execute',json={**confirm,'confirmed':False}).status_code==422
    executed=api.post('/api/mutations/execute',json=confirm)
    assert executed.status_code==200,executed.text
    assert executed.json()['status']=='VERIFIED_SUCCESS',executed.text
    assert all(row['status']=='VERIFIED_SUCCESS' for row in executed.json()['rows'])
    assert api.post('/api/mutations/execute',json=confirm).status_code==409
    assert api.get('/api/mutations/history').json()['items'][0]['id']==plan['id']


def test_stock_add_server_calc_and_no_client_target_substitution(api):
    request=payload('stocks')
    request['request'].update(stock_mode='add',value='5')
    prepared=api.post('/api/mutations/preview',json=request)
    assert prepared.status_code==200,prepared.text
    plan=prepared.json()
    assert plan['rows'][0]['before']==10 and plan['rows'][0]['after']==15
    assert api.post('/api/mutations/execute',json={'plan_id':plan['id'],'plan_hash':plan['hash'],'confirmed':True,'after':0}).status_code==422
    request['request']['value']='-1'
    assert api.post('/api/mutations/preview',json=request).status_code==422


def test_account_switch_invalidates_plan_and_keys_never_echo(api):
    plan=api.post('/api/mutations/preview',json=payload('stocks')).json()
    added=api.post('/api/accounts',json={'action':'add','name':'Второй MOCK','client_id':'123','api_key':'SYNTHETIC-DO-NOT-ECHO'})
    assert added.status_code==200
    assert 'SYNTHETIC-DO-NOT-ECHO' not in added.text and 'api_key' not in added.text
    account=next(row for row in added.json()['items'] if row['name']=='Второй MOCK')
    assert api.post('/api/accounts',json={'action':'select','id':account['id']}).status_code==200
    assert api.post('/api/mutations/execute',json={'plan_id':plan['id'],'plan_hash':plan['hash'],'confirmed':True}).status_code==409
    invalid=api.post('/api/accounts',json={'action':'add','name':'X','api_key':'SECRET-INVALID-INPUT','client_id':123})
    assert invalid.status_code==422 and 'SECRET-INVALID-INPUT' not in invalid.text


def test_remove_then_readd_same_mock_item(api):
    remove=payload('action_remove')
    plan=api.post('/api/mutations/preview',json=remove).json()
    assert api.post('/api/mutations/execute',json={'plan_id':plan['id'],'plan_hash':plan['hash'],'confirmed':True}).json()['status']=='VERIFIED_SUCCESS'
    add=payload('action_add')
    add['request']['ids']=remove['request']['ids']
    plan_response=api.post('/api/mutations/preview',json=add)
    assert plan_response.status_code==200,plan_response.text
    plan=plan_response.json()
    assert api.post('/api/mutations/execute',json={'plan_id':plan['id'],'plan_hash':plan['hash'],'confirmed':True}).json()['status']=='VERIFIED_SUCCESS'
