"""One child, Windows Job memory limit, no restart, total wall-clock watchdog.

The child cannot proceed until the Job assignment succeeds. Only offline_worker is
launchable, and every output path must be inside the candidate test directory.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from bounded_runner import ROOT, isolated_path
from windows_limits import WindowsJob, process_memory


def supervise(directory, *, scenario='collect', seconds=85, memory_mb=256):
    directory=isolated_path(directory)
    if scenario not in ('collect','hang','memory','crash','resume') or not 0<seconds<=85:
        raise ValueError('OFFLINE_SUPERVISOR_ARGUMENT')
    directory.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    env=dict(os.environ)
    for key in list(env):
        if any(s in key.upper() for s in ('TOKEN','SECRET','SERVICE_KEY','API_KEY','PROVENANCE')):
            env.pop(key,None)
    env['PYTHONDONTWRITEBYTECODE']='1'
    reason='EXITED'
    peak=0
    job=None
    proc=None
    with (directory/'worker.log').open('w',encoding='utf-8') as log:
        try:
            proc=subprocess.Popen([sys.executable,'-B',str(ROOT/'offline_worker.py'),str(directory),scenario],
                stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,cwd=ROOT,env=env,
                creationflags=subprocess.CREATE_NO_WINDOW)
            job=WindowsJob(proc,memory_mb)
            proc.stdin.write(b'GO\n')
            proc.stdin.flush()
            proc.stdin.close()
            while proc.poll() is None:
                if time.monotonic()-started>=seconds:
                    reason='DEADLINE_KILLED_NO_RETRY'
                    job.close()
                    break
                try:
                    peak=max(peak,process_memory(int(proc._handle))['private_bytes'])
                except OSError:
                    pass
                time.sleep(0.05)
            proc.wait(timeout=max(0.001,min(3,started+89-time.monotonic())))
        except Exception as exc:
            reason='SUPERVISOR_'+type(exc).__name__
        finally:
            if job:
                job.close()
            if proc and proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=max(0.001,min(1,started+89-time.monotonic())))
                except subprocess.TimeoutExpired:
                    reason='TERMINATION_UNCONFIRMED'
    elapsed=time.monotonic()-started
    result={'mode':'OFFLINE_FIXTURE','scenario':scenario,'reason':reason,'elapsed_seconds':round(elapsed,3),
            'under_90_seconds':elapsed<90,'process_exited':bool(proc and proc.poll() is not None),
            'exit_code':proc.returncode if proc else None,'job_memory_limit_mb':memory_mb,
            'peak_private_bytes':peak,'spawn_count':int(proc is not None),'retry_count':0,'live_calls':0}
    (directory/'supervisor.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Offline-only G2B supervised reproduction')
    parser.add_argument('--directory',required=True)
    parser.add_argument('--scenario',choices=['collect','hang','memory','crash','resume'],default='collect')
    parser.add_argument('--seconds',type=float,default=85)
    parser.add_argument('--memory-mb',type=int,default=256)
    args=parser.parse_args()
    print(json.dumps(supervise(args.directory,scenario=args.scenario,seconds=args.seconds,memory_mb=args.memory_mb)))
