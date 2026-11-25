"""Command line for the central service: `python -m central init-db` and `python -m central create-admin --email X`.

The password is read twice with getpass; there is deliberately no option that takes it on the command line,
where it would land in shell history and process listings.
"""
import argparse
import getpass
import os
import sys

from . import accounts, db, passwords


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m central", description="auto-runner central service")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("create-admin", help="create an admin account (asks for the password twice)")
    admin.add_argument("--email", required=True)
    supervisor = sub.add_parser("supervisor-create", help="create a supervisor account (asks for the password twice)")
    supervisor.add_argument("email")
    sub.add_parser("init-db", help="apply schema.sql to DATABASE_URL and add columns an older database lacks (safe to run again)")
    return parser


def init_db(url, out=sys.stdout, err=sys.stderr):
    try:
        added = db.init_db(url)
    except db.DuplicateRepositories as exc:
        print("stopped, nothing changed: one project per repository, but these projects share one:", file=err)
        for key, slugs in sorted(exc.args[0].items()):
            print("  %s: %s" % (key, ", ".join(slugs)), file=err)
        print("Keep one project per repository (move its members and tokens, delete the others), then run init-db again.",
              file=err)
        return 3
    except ValueError as exc:
        print(str(exc), file=err)
        return 2
    print("schema applied (%d tables)" % len(db.TABLES), file=out)
    for column in added:
        print("added column %s" % column, file=out)
    return 0


def create_admin(email, url, out=sys.stdout, err=sys.stderr, role="admin"):
    first = getpass.getpass("Password: ")
    if getpass.getpass("Password again: ") != first:
        print("the two passwords differ; nothing was created", file=err)
        return 1
    try:
        passwords.check_policy(first)
    except passwords.PasswordError as exc:
        print(str(exc), file=err)
        return 1
    conn = db.connect(url)
    try:
        account = accounts.create(conn, email, first, role=role)
    except accounts.AccountError as exc:
        print(str(exc), file=err)
        return 1
    finally:
        conn.close()
    print("created %s %s" % (role, account["email"]), file=out)
    return 0


def main(argv=None, environ=None, out=sys.stdout, err=sys.stderr):
    args = build_parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    url = environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=err)
        return 2
    if args.command == "create-admin":
        return create_admin(args.email, url, out=out, err=err)
    if args.command == "supervisor-create":
        return create_admin(args.email, url, out=out, err=err, role=accounts.SUPERVISOR)
    if args.command == "init-db":
        return init_db(url, out=out, err=err)
    return 2
