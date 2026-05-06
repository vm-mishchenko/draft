from pipeline import Step


class WorktreeCreateStep(Step):
    name = "worktree-create"

    def defaults(self) -> dict:
        return {"max_retries": 1, "timeout": 60, "retry_delay": 0}

    def cmd(self, ctx) -> list[str]:
        return ["git", "worktree", "add", ctx.get("wt_dir"), "-b", ctx.get("branch"), ctx.get("base_branch")]

    def run(self, ctx, engine, lifecycle):
        cfg = ctx.config(self.name)
        from pipeline import StepError
        rc = engine.run_stage(
            label=self.name,
            cmd=self.cmd(ctx),
            cwd=ctx.get("repo"),
            log_path=ctx.log_path(self.name),
            attempt=1,
            timeout=cfg["timeout"],
        )
        if rc != 0:
            raise StepError(self.name, rc)
