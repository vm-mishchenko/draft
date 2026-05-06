# draft

CLI tool that takes a spec file and runs it through an AI-powered pipeline to create a PR.

## Install

- Clone and `cd` into the repo
- Run `make setup` and put the venv on your PATH (the command prints where it is)
- Nuclear option: `make clean && make setup`

## Usage

```
draft list
draft create <spec-path>
draft create --prompt "TEXT"
draft continue [run-id]
draft delete <run-id>
```

## Config

Project config: `.draft/config.yaml`
Global config: `~/.draft/config.yaml`

```yaml
steps:
  code-spec:
    max_retries: 5
    timeout: 600
    hooks:
      pre:
        - cmd: "echo starting"
          timeout: 5
      on_error:
        - cmd: "echo failed"
          timeout: 10
          retry: 2
  pr-open:
    title_prefix: "CLOUDP-12345: "
```
