"""Shared per-account IM request pacing for current send and read transports."""
import threading
import time


class RequestBudget:
    """Aggregate IM reads and writes so concurrent lanes cannot exceed one account budget."""
    def __init__(self,qps=3,*,clock=time.monotonic,sleep=time.sleep):
        if type(qps) not in (int,float) or not 1<=qps<=3:
            raise ValueError('invalid_request_budget')
        self.interval,self.clock,self.sleep=1/qps,clock,sleep
        self.lock=threading.Lock();self.next_at=0;self.requests=0;self.wait_seconds=0

    def acquire(self):
        with self.lock:
            delay=max(0,self.next_at-self.clock());self.wait_seconds+=delay
            self.sleep(delay);self.requests+=1
            self.next_at=self.clock()+self.interval
