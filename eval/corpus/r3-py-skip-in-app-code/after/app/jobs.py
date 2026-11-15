def run(job, runner):
    if job.get("done"):
        runner.skip("already processed")
        return None
    return runner.run(job)
