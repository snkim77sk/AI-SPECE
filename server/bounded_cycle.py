"""Bound preparation, collection and final readback in one Windows process tree."""
import os
import subprocess
import time


def run_bounded(command, *, cwd, log, started, max_seconds=85):
    from windows_limits import WindowsJob, process_memory
    env=dict(os.environ)
    for key in list(env):
        if any(part in key.upper() for part in ('TOKEN','SECRET','SERVICE_KEY','API_KEY','PROVENANCE')):
            env.pop(key,None)
    env['PYTHONDONTWRITEBYTECODE']='1'
    process=None
    job=None
    timed_out=False
    peak=0
    job_memory={}
    try:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,
            cwd=cwd,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
        # Children inherit this outer job. The combined controller/worker private
        # commit is capped as well as each process, including preparation.
        job=WindowsJob(process,256,total_memory_mb=256)
        process.stdin.write(b'GO\n');process.stdin.flush();process.stdin.close()
        while process.poll() is None:
            if time.monotonic()-started>=max_seconds:
                timed_out=True
                break
            try:
                peak=max(peak,process_memory(int(process._handle))['private_bytes'])
            except OSError:
                pass
            time.sleep(0.05)
    finally:
        if job:
            try:
                job_memory=job.memory_usage()
            except OSError:
                pass
            job.close()
        if process and process.poll() is None:
            process.kill()
        if process:
            process.wait(timeout=1)
    return {'timed_out':timed_out,'controller_exit_code':process.returncode,
            'controller_peak_private_bytes':peak,'process_tree_stopped':True,**job_memory}
