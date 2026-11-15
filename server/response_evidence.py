"""Credential-free HTTP shape evidence. Never write URLs, headers or source text."""
import hashlib
import json


def summarize(raw, http_status=None, content_type=''):
    result={'body_sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
            'http_status':http_status,'content_type':str(content_type).split(';')[0][:80]}
    try:
        data=json.loads(raw)
        env=data.get('response',data) if isinstance(data,dict) else {}
        header=env.get('header',{})
        body=env.get('body')
        code=str(header.get('resultCode',''))
        result['result_code']=code if len(code)<=8 and code.isalnum() else None
        if isinstance(body,dict):
            result['body_keys']=sorted(str(k)[:80] for k in body)
            result['items_present']='items' in body
            result['items_type']=type(body.get('items')).__name__
            for name in ('totalCount','pageNo','numOfRows'):
                value=body.get(name)
                if type(value) is int or isinstance(value,str) and len(value)<20 and value.isdigit():
                    result[name]=value
            if 'items' not in body and set(body)<={'totalCount','pageNo','numOfRows'}:
                result['reproducible_envelope']={'response':{'header':{'resultCode':result['result_code']},
                    'body':{k:result[k] for k in body if k in result}}}
    except (ValueError,TypeError,AttributeError,UnicodeError):
        result['json_envelope']=False
    return result
