"""4.1.252: async web handlers offload DB and one-shot source HTTP safely."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time

import pytest
from starlette.requests import Request

import vnext_clean_app as app


def post(path, fields=""):
    async def receive():
        return {"type":"http.request","body":fields.encode(),"more_body":False}
    return Request({
        "type":"http","path":path,"method":"POST",
        "headers":[(b"content-type",b"application/x-www-form-urlencoded")],
        "query_string":b"", "client":("127.0.0.1",1234),
    },receive)


def test_slow_login_db_does_not_block_http_event_loop(monkeypatch):
    monkeypatch.setattr(app,"_WEB_ASYNC_POST_SLOTS",threading.BoundedSemaphore(2))
    monkeypatch.setattr(app,"_login_allowed",lambda keys:True)
    monkeypatch.setattr(app,"_login_success",lambda keys:None)
    monkeypatch.setattr(app,"create_session",lambda user:"TOKEN")
    started=threading.Event()
    release=threading.Event()
    thread_ids=[]
    def slow_auth(username,password):
        thread_ids.append(threading.get_ident())
        started.set()
        assert release.wait(4)
        return {"username":"operator","role":"admin"}
    monkeypatch.setattr(app,"authenticate",slow_auth)
    async def scenario():
        loop_id=threading.get_ident()
        task=asyncio.create_task(app.login_submit(post(
            "/login","username=operator&password=correct"
        )))
        assert await asyncio.to_thread(started.wait,2)
        before=time.monotonic()
        await asyncio.sleep(0.04)
        assert time.monotonic()-before<0.3
        assert not task.done()
        assert thread_ids[0]!=loop_id
        release.set()
        response=await asyncio.wait_for(task,3)
        assert response.status_code==302
        assert response.headers["location"]=="/dashboard"
        assert "TOKEN" in response.headers.get("set-cookie","")
    asyncio.run(scenario())


def test_unauthorized_source_command_never_launches_collector(monkeypatch):
    monkeypatch.setattr(app,"_WEB_ASYNC_POST_SLOTS",threading.BoundedSemaphore(2))
    monkeypatch.setattr(app,"require_user",lambda request:None)
    monkeypatch.setattr(app,"schedule_manual_collection",lambda *_:(_ for _ in ()).throw(
        AssertionError("collector must not start")
    ))
    response=asyncio.run(app.collect_shopping_recent(post("/collect/shopping-recent")))
    assert response.status_code==302
    assert response.headers["location"]=="/login"


def test_source_quota_context_and_fetch_run_on_same_offloaded_thread(monkeypatch):
    monkeypatch.setattr(app,"_WEB_ASYNC_POST_SLOTS",threading.BoundedSemaphore(2))
    monkeypatch.setattr(app,"require_user",lambda request:{"username":"operator","role":"admin"})
    monkeypatch.setattr(app,"is_result_server",lambda:False)
    monkeypatch.setattr(app,"valid_csrf",lambda *_:True)
    monkeypatch.setattr(app,"get_service_key",lambda *_:"CI_KEY")
    import shopping_vnext
    import vnext_source_guard
    marker=ContextVar("quota_active",default=False)
    events=[]
    @contextmanager
    def guarded(**kw):
        token=marker.set(True)
        try:
            events.append(("guard",threading.get_ident(),kw["max_requests"]))
            yield
        finally:
            marker.reset(token)
    def fetch(*_args,**_kwargs):
        assert marker.get() is True
        events.append(("fetch",threading.get_ident(),True))
        return {},0
    monkeypatch.setattr(vnext_source_guard,"operational_recent_source_context",guarded)
    monkeypatch.setattr(shopping_vnext,"fetch_page",fetch)
    async def scenario():
        main_thread=threading.get_ident()
        response=await app.settings_probe_source(post(
            "/settings/probe-source","source=g2b&_csrf=any"
        ))
        assert response.status_code==303
        assert response.headers["location"]=="/settings?saved=probe_g2b"
        assert events[0][1]!=main_thread
    asyncio.run(scenario())
    assert events==[("guard",events[0][1],1),("fetch",events[0][1],True)]


def test_two_workers_max_and_cancelled_http_retains_worker_slot(monkeypatch):
    monkeypatch.setattr(app,"_WEB_ASYNC_POST_SLOTS",threading.BoundedSemaphore(2))
    entered=threading.Event()
    unblock=threading.Event()
    lock=threading.Lock()
    count=[0]
    def blocked():
        with lock:
            count[0]+=1
            if count[0]==2:entered.set()
        assert unblock.wait(4)
        return "ok"
    async def scenario():
        a=asyncio.create_task(app._run_web_async_io(blocked))
        b=asyncio.create_task(app._run_web_async_io(blocked))
        assert await asyncio.to_thread(entered.wait,2)
        with pytest.raises(app._WebAsyncPostBusy):
            await app._run_web_async_io(lambda:"wrong")
        a.cancel()
        with pytest.raises(asyncio.CancelledError):
            await a
        with pytest.raises(app._WebAsyncPostBusy):
            await app._run_web_async_io(lambda:"wrong")
        unblock.set()
        assert await asyncio.wait_for(b,3)=="ok"
        for _ in range(100):
            if app._WEB_ASYNC_POST_SLOTS._value==2: break
            await asyncio.sleep(0.01)
        assert app._WEB_ASYNC_POST_SLOTS._value==2
        assert await app._run_web_async_io(lambda:"free")=="free"
    asyncio.run(scenario())


def test_excess_post_returns_retry_response_without_session_mutation(monkeypatch):
    monkeypatch.setattr(app,"TEST_MODE",False)
    async def scenario():
        response=await app._web_async_post_busy_handler(
            post("/login"),app._WebAsyncPostBusy("secret")
        )
        assert response.status_code==503
        assert response.headers["retry-after"]=="2"
        assert "set-cookie" not in response.headers
        assert "location" not in response.headers
        assert b"secret" not in response.body
        api=await app._web_async_post_busy_handler(
            post("/api/result-sync"),app._WebAsyncPostBusy("secret")
        )
        assert api.status_code==503
        assert b"WEB_ASYNC_IO_BUSY_RETRY" in api.body
    asyncio.run(scenario())


def test_preserve_4250_pool_and_4247_initial_budget_schema():
    import inspect
    import db
    import g2b_database
    assert "g2b_database.ensure_schema(g2b_database.app_schema())" in inspect.getsource(db.init_db)
    assert "budget_boot_ok = bool(_boot_budget_storage.storage_ready())" in inspect.getsource(app.initialize_backend)
    source=inspect.getsource(g2b_database.engine)
    assert '_env_int("G2B_DB_POOL_SIZE", 1, lower=1, upper=2)' in source
    assert '_env_int("G2B_DB_MAX_OVERFLOW", 1, lower=0, upper=1)' in source


def test_source_probe_overload_propagates_http_503_instead_of_api_failure(monkeypatch):
    monkeypatch.setattr(app,"require_user",lambda _:{"username":"operator","role":"admin"})
    monkeypatch.setattr(app,"is_result_server",lambda:False)
    monkeypatch.setattr(app,"valid_csrf",lambda *_:True)
    async def fake_offload(function,*args,**kwargs):
        if function is app.require_user:
            return {"username":"operator","role":"admin"}
        assert function is app._probe_source_connection
        raise app._WebAsyncPostBusy("never call source API")
    monkeypatch.setattr(app,"_run_web_async_io",fake_offload)
    with pytest.raises(app._WebAsyncPostBusy):
        asyncio.run(app.settings_probe_source(post(
            "/settings/probe-source","source=g2b&_csrf=valid"
        )))
