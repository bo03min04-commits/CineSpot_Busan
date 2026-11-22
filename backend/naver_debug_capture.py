"""Opt-in visible-page diagnostics. No screenshots, credentials or browser profiles."""
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

LOCAL=threading.local()

def event(kind,**fields):
    report=getattr(LOCAL,'report',None)
    if report is None:return
    item={'kind':kind,**fields}
    if not report['events'] or item!=report['events'][-1]:
        report['events'].append(item)
        report['events']=report['events'][-24:]

@contextmanager
def session(url,name,address,phone):
    directory=os.getenv('NAVER_DEBUG_DIR','')
    if not directory:
        yield
        return
    report={'request':{'url':url,'name':name,'address':address,'phone':phone},'events':[],'success':False}
    LOCAL.report=report
    try:
        yield
        report['success']=True
    except Exception as error:
        report['error_type']=type(error).__name__
        report['error']=str(error)[:1000]
        raise
    finally:
        LOCAL.report=None
        try:
            folder=Path(directory);folder.mkdir(parents=True,exist_ok=True)
            (folder/f'trace_{time.time_ns()}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        except OSError:
            print('TRACE_SAVE_FAILED: diagnostic file could not be written',flush=True)
