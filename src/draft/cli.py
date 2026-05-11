import argparse
import sys

from draft import command_create, command_continue, command_delete, command_init, command_list, command_prune, command_status


def main():
    parser = argparse.ArgumentParser(
        prog="draft",
        description="Run a spec through an AI-powered pipeline and open a PR.",
    )
    subs = parser.add_subparsers(dest="command")
    command_create.register(subs)
    command_init.register(subs)
    command_list.register(subs)
    command_continue.register(subs)
    command_delete.register(subs)
    command_prune.register(subs)
    command_status.register(subs)

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(2)

    sys.exit(args.func(args))
