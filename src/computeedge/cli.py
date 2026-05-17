import argparse
import asyncio
import secrets
from pathlib import Path

from computeedge.state.database import Database


async def create_user(db_path: str, name: str):
    db = Database(db_path)
    await db.initialize()
    api_key = f"ce_{secrets.token_urlsafe(32)}"
    user_id = await db.create_user(name, api_key)
    await db.close()
    print(f"User created: {name} (id={user_id})")
    print(f"API key: {api_key}")
    print("Store this key securely — it cannot be retrieved later.")


async def list_users(db_path: str):
    db = Database(db_path)
    await db.initialize()
    cursor = await db._db.execute("SELECT id, name, created_at FROM users")
    rows = await cursor.fetchall()
    await db.close()
    if not rows:
        print("No users found.")
        return
    for row in rows:
        print(f"  id={row['id']}  name={row['name']}  created={row['created_at']}")


def main():
    parser = argparse.ArgumentParser(prog="computeedge-admin", description="ComputeEdge admin CLI")
    parser.add_argument("--db", default=str(Path.home() / ".computeedge" / "computeedge.db"),
                        help="Path to SQLite database")
    sub = parser.add_subparsers(dest="command")

    create_p = sub.add_parser("create-user", help="Create a new API key")
    create_p.add_argument("--name", required=True, help="User display name")

    sub.add_parser("list-users", help="List all users")

    args = parser.parse_args()
    if args.command == "create-user":
        asyncio.run(create_user(args.db, args.name))
    elif args.command == "list-users":
        asyncio.run(list_users(args.db))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
