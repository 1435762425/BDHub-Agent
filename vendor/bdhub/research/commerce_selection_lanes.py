"""同账号选入专用的有限并发；每组在途请求收齐后才允许处理验证。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time

from bdhub.send.sharelink.transport import BrowserResult


class DispatchCancelled(RuntimeError):
    pass


class RequestGate:
    def __init__(self,qps,stop,*,clock=time.monotonic,sleep=time.sleep):
        if not 0<qps<=2:raise ValueError('commerce_selection_qps_invalid')
        self.interval=1/qps;self.stop=stop;self.clock=clock;self.sleep=sleep
        self.lock=Lock();self.next_at=0

    def acquire(self,*,check_stop=True):
        with self.lock:
            if check_stop and self.stop():raise DispatchCancelled()
            delay=self.next_at-self.clock()
            if delay>0:self.sleep(delay)
            if check_stop and self.stop():raise DispatchCancelled()
            self.next_at=self.clock()+self.interval


class SelectionLanes:
    def __init__(self,transport,stop,*,concurrency=2,qps=2):
        if concurrency not in {1,2}:raise ValueError('commerce_selection_concurrency_invalid')
        self.transport=transport;self.stop=stop
        self.width=concurrency if getattr(getattr(transport,'identity',None),'market',None)=='it' and callable(getattr(transport,'selection_lane',None)) else 1
        self.qps=qps if self.width>1 else 1
        self.gate=RequestGate(self.qps,stop);self.lanes=[];self.executor=None

    def __enter__(self):
        self.old_pace=getattr(self.transport,'_pace',None)
        # 主会话仅用于读取和验证，写通道有各自的Session；总请求共用一个节拍。
        self.transport._pace=lambda:self.gate.acquire(check_stop=False)
        try:
            if self.width>1:
                for _ in range(self.width):self.lanes.append(self.transport.selection_lane(self.gate.acquire))
                self.executor=ThreadPoolExecutor(max_workers=self.width)
            else:self.lanes=[self.transport]
        except Exception:
            self.__exit__(None,None,None);raise
        return self

    def __exit__(self,*_):
        if self.executor:self.executor.shutdown(wait=True,cancel_futures=True)
        for lane in self.lanes:
            if lane is not self.transport:lane.session.close()
        if self.old_pace is not None:self.transport._pace=self.old_pace

    def _send(self,lane,item,cid):
        start=time.monotonic()
        original_pace=lane._pace
        lane._pace=self.gate.acquire
        try:
            if self.stop():raise DispatchCancelled()
            result=lane.select_product(item['pid'],cid)
        except DispatchCancelled:
            result=BrowserResult('not_dispatched',0,{},False,False)
        except Exception:
            result=BrowserResult('worker_error',0,{},True,True)
        finally:lane._pace=original_pace
        return item,cid,lane,result,round(time.monotonic()-start,3)

    def pairs(self,plans,begin):
        for offset in range(0,len(plans),self.width):
            pair=plans[offset:offset+self.width]
            if self.stop() or not begin(pair):return
            if self.executor:
                futures=[self.executor.submit(self._send,self.lanes[index],item,cid) for index,(item,cid) in enumerate(pair)]
                outcomes=[future.result() for future in futures]
            else:
                item,cid=pair[0];outcomes=[self._send(self.transport,item,cid)]
            # yield时所有写入已结束；调用方处理验证期间不会发出下一组。
            yield outcomes

    def adopt_verified_session(self,source):
        if source is not self.transport:self.transport.copy_session_from(source)
        for lane in self.lanes:
            if lane is not source and lane is not self.transport:lane.copy_session_from(source)
