"""Synthetic HTTP envelopes only; no production payloads, credentials or network."""
import io
import json
import socket
import urllib.parse
import urllib.request
import urllib.error
from contextlib import contextmanager, ExitStack
from unittest.mock import patch


class FixtureSource:
    offline_only=True

    def __init__(self,*,total=18000,page_size=999,payload_bytes=0,behaviors=None):
        self.total=total
        self.page_size=page_size
        self.payload_bytes=payload_bytes
        self.behaviors=behaviors or {}
        self.calls=[]
        self.network_denials=0

    def blocked(self,*args,**kwargs):
        self.network_denials+=1
        raise AssertionError('NETWORK_FORBIDDEN_OFFLINE')

    def open(self,request,timeout=None):
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        day=query['inqryBgnDate'][0]
        day=f'{day[:4]}-{day[4:6]}-{day[6:8]}'
        page=int(query['pageNo'][0])
        size=int(query['numOfRows'][0])
        self.calls.append({'day':day,'page':page,'timeout':timeout})
        behavior=self.behaviors.get((day,page),self.behaviors.get(page,'ok'))
        if behavior=='timeout':
            raise TimeoutError('FIXTURE_TIMEOUT')
        if behavior=='crash':
            import os
            os._exit(77)
        if behavior=='hang':
            import time
            time.sleep(120)
        if behavior=='malformed':
            return io.BytesIO(b'{')
        if behavior=='oversize':
            return io.BytesIO(b'x'*(16*1024*1024+1))
        if behavior=='rate_limit':
            return io.BytesIO(b'{"response":{"header":{"resultCode":"23"}}}')
        if behavior=='http_error':
            raise urllib.error.HTTPError('https://offline.invalid/',429,'FIXTURE',{},io.BytesIO(b'{}'))
        offset=(page-1)*size
        rows=[{'dlvrReqNo':f'FIXTURE-{day}-{i}','prdctSno':'1','dlvrReqChgOrd':'0',
               'dlvrReqRcptDate':day.replace('-',''),'description':'x'*self.payload_bytes}
              for i in range(offset,min(offset+size,self.total))]
        if behavior=='duplicate' and rows:
            rows[-1]=dict(rows[0])
        if behavior=='overlap' and rows:
            rows[0]['dlvrReqNo']=f'FIXTURE-{day}-0'
            rows[0]['description']='invalid changed payload must not overwrite RAW'
        total=self.total+1 if behavior=='changed_total' else self.total
        payload={'response':{'header':{'resultCode':'00'},'body':{'items':rows,'totalCount':total}}}
        return io.BytesIO(json.dumps(payload).encode())

    @contextmanager
    def installed(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(urllib.request,'urlopen',self.open))
            stack.enter_context(patch.object(socket.socket,'connect',self.blocked))
            stack.enter_context(patch.object(socket,'create_connection',self.blocked))
            stack.enter_context(patch.object(socket,'getaddrinfo',self.blocked))
            yield self
