import subprocess
import time

from pipeline import Step, StepError


class PrViewStep(Step):
    name = "pr-view"

    def defaults(self) -> dict:
        return {"max_retries": 3, "timeout": 30, "retry_delay": 5}

    def run(self, ctx, engine, lifecycle):
        cfg = ctx.config(self.name)
        wt_dir = ctx.get("wt_dir")

        for attempt in range(1, cfg["max_retries"] + 1):
            try:
                result = subprocess.run(
                    ["gh", "pr", "view", "--json", "url", "-q", ".url"],
                    capture_output=True, text=True,
                    cwd=wt_dir,
                    timeout=cfg["timeout"],
                )
                url = result.stdout.strip()
                if url:
                    ctx.set("pr_url", url)
                    ctx.save()
                    return
            except subprocess.TimeoutExpired:
                pass

            if attempt < cfg["max_retries"]:
                time.sleep(cfg["retry_delay"])

        raise StepError(self.name, 1)
