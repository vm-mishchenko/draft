from pipeline import Step, StepError


class PushStep(Step):
    name = "push-commits"

    def defaults(self) -> dict:
        return {"max_retries": 1, "timeout": 120, "retry_delay": 0}

    def cmd(self, ctx) -> list[str]:
        return ["git", "push", "-u", "origin", "HEAD"]

    def run(self, ctx, runner, lifecycle):
        cfg = ctx.config(self.name)
        with runner.stage(self.name):
            rc = runner.run_command(
                cmd=self.cmd(ctx),
                cwd=ctx.get("wt_dir"),
                log_path=ctx.log_path(self.name),
                attempt=1,
                timeout=cfg["timeout"],
            )
            if rc != 0:
                raise StepError(self.name, rc)
