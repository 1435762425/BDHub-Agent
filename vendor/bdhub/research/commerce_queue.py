"""商品只读采集等待同账号租约，不把正常占用记成网络失败。"""
from contextlib import contextmanager, ExitStack
import time

from bdhub.enrich.profile_lease import ProfileBusyError
from bdhub.hub.file_intents import now


@contextmanager
def queued_transport(job, repository, factory, *, sleep=time.sleep, monotonic=time.monotonic, timeout=1800):
    deadline=monotonic()+timeout
    with ExitStack() as stack:
        while True:
            if (repository.root/f"{job['job_id']}.stop").exists():
                raise ValueError('commerce_stopped')
            try:
                transport=stack.enter_context(factory(job))
                break
            except ProfileBusyError:
                job.update(state='waiting_account',error='',wait_reason='该账号正在执行其他任务，释放后自动继续')
                job.setdefault('wait_started_at',now())
                repository.save(job)
                if monotonic()>=deadline:
                    raise ValueError('commerce_account_wait_timeout') from None
                sleep(2)
        job.update(state='running',wait_reason='',error='')
        repository.save(job)
        yield transport
