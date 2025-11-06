import asyncio
from save_user import mcp

async def main():
    result = await mcp.call_tool("save_user", {
        "contact_name": "Vinh",
        "contact_phone": "0909123456",
        "session_id": "abc123"
    })

    print(result)

if __name__ == "__main__":
    asyncio.run(main())
